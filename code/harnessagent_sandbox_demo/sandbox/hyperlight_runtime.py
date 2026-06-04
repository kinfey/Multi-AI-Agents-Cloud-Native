"""Hyperlight Wasm sandbox runtime.

Wraps `hyperlight_sandbox.Sandbox` with the snapshot/restore pattern from
`hyperlight-sandbox/examples/agent-framework/copilot_agent.py`, and exposes an
`execute_code` tool factory that every agent in the workflow shares.

Each agent gets a clean snapshot before running code, so state cannot leak
between agent turns. The host pays the cold start (~680 ms) once.

Thread model
------------
`hyperlight_sandbox.Sandbox` (the WASM backend) is `!Send` on the Rust side:
every call — construction, `allow_domain`, `run`, `snapshot`, `restore` —
must happen on the **same OS thread**. We therefore pin all sandbox work to
a single-worker `ThreadPoolExecutor` and dispatch from asyncio with
`loop.run_in_executor(self._executor, ...)`.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

try:
    from hyperlight_sandbox import Sandbox
except ImportError as exc:  # pragma: no cover - runtime guard
    raise ImportError(
        "hyperlight_sandbox is not installed. Build and install the Python SDK "
        "from https://github.com/hyperlight-dev/hyperlight-sandbox "
        "(see README.md for instructions)."
    ) from exc


# Domains the sandbox is allowed to reach. FIFA.com is the primary source.
DEFAULT_ALLOWED_DOMAINS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("https://www.fifa.com", ("GET",)),
    ("https://inside.fifa.com", ("GET",)),
    ("https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026", ("GET",)),
    ("https://api.fifa.com", ("GET",)),
    # Secondary sources for cross-referencing news / friendlies / colour pieces
    ("https://www.uefa.com", ("GET",)),
    ("https://www.concacaf.com", ("GET",)),
    ("https://www.conmebol.com", ("GET",)),
    ("https://www.afc-asia.com", ("GET",)),
    ("https://www.bbc.com", ("GET",)),
    ("https://www.espn.com", ("GET",)),
    ("https://www.reuters.com", ("GET",)),
)


class HyperlightRuntime:
    """Singleton-style wrapper around a single Hyperlight Wasm sandbox."""

    def __init__(
        self,
        module_path: str | os.PathLike[str] | None = None,
        output_dir: str | os.PathLike[str] | None = None,
        allowed_domains: Iterable[tuple[str, tuple[str, ...]]] = DEFAULT_ALLOWED_DOMAINS,
    ) -> None:
        raw_module_path = module_path or os.environ.get("HYPERLIGHT_PYTHON_MODULE_PATH", "")
        self._module_path = Path(raw_module_path).expanduser() if raw_module_path else None
        self._output_dir = Path(output_dir or os.environ.get("PODCAST_OUTPUT_DIR", "./outputs"))
        self._allowed_domains = tuple(allowed_domains)

        self._sandbox: Sandbox | None = None
        self._snapshot = None
        self._input_tmp: tempfile.TemporaryDirectory | None = None
        self._lock = asyncio.Lock()
        # Sandbox is `!Send`: every call must happen on the same OS thread.
        self._executor: ThreadPoolExecutor | None = None

    @property
    def output_dir(self) -> Path:
        """Host path mounted into the sandbox as `/output`."""
        return self._output_dir

    # ----------------------------- lifecycle -----------------------------

    def init(self) -> None:
        """Build the sandbox, register tools, warm it up, then snapshot."""
        if self._sandbox is not None:
            return

        if self._module_path is None:
            raise RuntimeError(
                "HYPERLIGHT_PYTHON_MODULE_PATH is not set.\n"
                "Add it to your .env, pointing at the absolute path of the built "
                "python-sandbox.aot guest module, e.g.\n"
                r"  HYPERLIGHT_PYTHON_MODULE_PATH=C:\path\to\hyperlight-sandbox\src\wasm_sandbox\guests\python\python-sandbox.aot"
            )
        if not self._module_path.is_file():
            raise RuntimeError(
                "Hyperlight Wasm guest module not found (or is not a file).\n"
                f"  HYPERLIGHT_PYTHON_MODULE_PATH = {self._module_path}\n"
                "Build python-sandbox.aot via `just guest-build` in the "
                "hyperlight-sandbox repo, then set the env var to its absolute path."
            )

        self._output_dir = self._output_dir.expanduser().resolve()
        self._output_dir.mkdir(parents=True, exist_ok=True)
        # Python 3.10+: swallow Windows handle-still-open errors on cleanup.
        self._input_tmp = tempfile.TemporaryDirectory(
            prefix="podcast-sandbox-input-",
            ignore_cleanup_errors=True,
        )

        # Spin up the single-worker executor that owns the sandbox.
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="hyperlight"
        )
        # Build, configure, warm-up, and snapshot — all on the worker thread.
        self._executor.submit(self._init_on_worker).result()

    def _init_on_worker(self) -> None:
        start = time.perf_counter()
        self._sandbox = Sandbox(
            backend="wasm",
            module_path=str(self._module_path.resolve()),  # type: ignore[union-attr]
            input_dir=self._input_tmp.name,  # type: ignore[union-attr]
            output_dir=str(self._output_dir),
        )

        for domain, methods in self._allowed_domains:
            self._sandbox.allow_domain(domain, methods=list(methods))

        # Warm-up run so the snapshot captures a ready interpreter.
        self._sandbox.run("None")
        self._snapshot = self._sandbox.snapshot()

        elapsed_ms = (time.perf_counter() - start) * 1000
        print(
            f"[hyperlight] sandbox ready ({elapsed_ms:.0f} ms, "
            f"output_dir={self._output_dir.resolve()})"
        )

    def shutdown(self) -> None:
        # Drop the sandbox on its own worker thread so Rust drop runs there.
        if self._executor is not None:
            try:
                self._executor.submit(self._shutdown_on_worker).result(timeout=5)
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                print(f"[hyperlight] worker shutdown error: {exc}", file=sys.stderr)
            self._executor.shutdown(wait=True)
            self._executor = None

        if self._input_tmp is not None:
            try:
                self._input_tmp.cleanup()
            except OSError as exc:
                print(f"[hyperlight] tmp cleanup error: {exc}", file=sys.stderr)
            self._input_tmp = None

    def _shutdown_on_worker(self) -> None:
        # Releasing references on the owning thread lets pyo3 drop cleanly.
        self._snapshot = None
        self._sandbox = None

    # ----------------------------- execution -----------------------------

    # The Hyperlight guest's shared output buffer is 16,376 bytes. If the
    # guest function tries to serialize a larger stdout/stderr the entire
    # guest aborts. We cap each stream inside the guest before serialization
    # so an over-eager `print()` from the model can never panic the sandbox.
    _STDOUT_LIMIT = 12_000
    _STDERR_LIMIT = 2_000

    def _wrap_with_caps(self, code: str) -> str:
        """Embed user code inside a guest-side stdout/stderr cap."""
        code_literal = json.dumps(code)
        return (
            "import sys as _sys\n"
            "class _Cap:\n"
            "    def __init__(self, limit, label):\n"
            "        self._chunks = []\n"
            "        self._len = 0\n"
            "        self._truncated = False\n"
            "        self._limit = limit\n"
            "        self._label = label\n"
            "    def write(self, s):\n"
            "        s = s if isinstance(s, str) else str(s)\n"
            "        n = len(s)\n"
            "        if self._truncated:\n"
            "            return n\n"
            "        if self._len + n > self._limit:\n"
            "            avail = self._limit - self._len\n"
            "            if avail > 0:\n"
            "                self._chunks.append(s[:avail])\n"
            "                self._len += avail\n"
            "            self._chunks.append("
            "'\\n...[' + self._label + ' truncated at ' + str(self._limit) + ' chars]\\n')\n"
            "            self._truncated = True\n"
            "        else:\n"
            "            self._chunks.append(s)\n"
            "            self._len += n\n"
            "        return n\n"
            "    def flush(self):\n"
            "        pass\n"
            "    def getvalue(self):\n"
            "        return ''.join(self._chunks)\n"
            f"_cap_out = _Cap({self._STDOUT_LIMIT}, 'stdout')\n"
            f"_cap_err = _Cap({self._STDERR_LIMIT}, 'stderr')\n"
            "_orig_out = _sys.stdout\n"
            "_orig_err = _sys.stderr\n"
            "_sys.stdout = _cap_out\n"
            "_sys.stderr = _cap_err\n"
            "try:\n"
            f"    _user_code = {code_literal}\n"
            "    exec(compile(_user_code, '<user>', 'exec'), {'__name__': '__main__'})\n"
            "finally:\n"
            "    _sys.stdout = _orig_out\n"
            "    _sys.stderr = _orig_err\n"
            "    _sys.stdout.write(_cap_out.getvalue())\n"
            "    _err_text = _cap_err.getvalue()\n"
            "    if _err_text:\n"
            "        _sys.stderr.write(_err_text)\n"
        )

    def _run_sync(self, code: str) -> str:
        if self._sandbox is None or self._snapshot is None:
            raise RuntimeError("HyperlightRuntime.init() must be called before execute_code().")

        self._sandbox.restore(self._snapshot)
        wrapped = self._wrap_with_caps(code)

        start = time.perf_counter()
        result = self._sandbox.run(code=wrapped)
        elapsed_ms = (time.perf_counter() - start) * 1000

        if result.success:
            stdout = (result.stdout or "").replace("\r\n", "\n")
            print(f"[hyperlight] execute_code OK ({elapsed_ms:.1f} ms, {len(stdout)} chars)")
            if not stdout:
                return "Code executed successfully (no stdout)."
            return (
                "The code ran successfully. Include this output verbatim in your "
                "next response:\n\n```\n" + stdout + "\n```"
            )

        stderr = result.stderr or "Unknown error"
        print(f"[hyperlight] execute_code FAILED ({elapsed_ms:.1f} ms)")
        return f"Execution error:\n{stderr}"

    async def execute_code_async(self, code: str) -> str:
        if self._executor is None:
            raise RuntimeError("HyperlightRuntime.init() must be called before execute_code().")
        async with self._lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self._executor, self._run_sync, code)
