"""Run the FIFA WC 2026 podcast pipeline as an Agent Framework workflow.

The graph is assembled in :mod:`workflow_pipeline` with `WorkflowBuilder`:
    prepare → SearchAgent(crawler) → adapt → ContentAgent → adapt
        → GenScriptAgent → save_scripts

All four agents are harness agents (`create_harness_agent`) backed by
`FoundryChatClient`. They share a single Hyperlight Wasm sandbox for code
execution, file I/O, and network fetches (FIFA.com first).

Run:
    az login
    python main.py
    python main.py --date 2026-06-01      # back-fill a specific episode date
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from sandbox import HyperlightRuntime
from workflow_pipeline import build_pipeline


ANSI_GREEN = "\033[32m"
ANSI_RED = "\033[31m"
ANSI_RESET = "\033[0m"

AGENT_TOOL_CONFIG: dict[str, list[str]] = {
    "prepare_search_prompt": [],
    # Each agent runs under HyperlightCodeActProvider. The model only sees
    # `execute_code`; `fetch_url` is the single host bridge reachable from
    # inside the sandbox via `call_tool(...)` because guest urllib stalls.
    "SearchAgent": ["execute_code", "fetch_url"],
    "adapt_search_to_content": [],
    "ContentAgent": ["execute_code", "fetch_url"],
    "adapt_content_to_genscript": [],
    "GenScriptAgent": ["execute_code"],
    "save_scripts": [],
}

KNOWN_TOOL_NAMES: tuple[str, ...] = (
    "execute_code",
    "fetch_url",
)


def _green(text: str) -> str:
    return f"{ANSI_GREEN}{text}{ANSI_RESET}"


def _red(text: str) -> str:
    return f"{ANSI_RED}{text}{ANSI_RESET}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FIFA WC 2026 daily podcast workflow")
    parser.add_argument(
        "--date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=None,
        help="Target episode date (YYYY-MM-DD). Defaults to today.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override the host output directory (defaults to PODCAST_OUTPUT_DIR or ./outputs).",
    )
    return parser.parse_args()


def _record_tool_call(state: dict[str, Any], author: str, tool_name: str) -> None:
    counters = state.setdefault("tool_call_counts", {})
    agent_counts = counters.setdefault(author, {})
    agent_counts[tool_name] = agent_counts.get(tool_name, 0) + 1


def _print_agent_update(event: Any, state: dict[str, Any]) -> None:
    """Pretty-print a streamed agent update, keyed by author.

    Tool-call accounting is handled by the per-agent function_middleware
    registered in `agents/common.py` (`make_tool_call_recorder`), which
    records both the model-visible `execute_code` calls and the inner
    `call_tool(...)` invocations the CodeAct provider routes back to host
    tools. This function therefore only prints text.
    """
    update = event.data
    author = getattr(update, "author_name", None) or getattr(event, "executor_id", "agent")

    text = getattr(update, "text", "") or ""
    if not text:
        return
    if state.get("last_author") != author:
        if state.get("last_author") is not None:
            print()
        print(f"\n[{author}] ", end="", flush=True)
        state["last_author"] = author
    print(text, end="", flush=True)


def _print_agent_tool_inventory() -> None:
    """Print the tool setup for each workflow node/agent."""
    print("\n=== Agent Tool Inventory ===")
    print("prepare_search_prompt: none (prompt adapter)")
    print("SearchAgent: execute_code  +  call_tool(fetch_url)")
    print("adapt_search_to_content: none (prompt adapter)")
    print("ContentAgent: execute_code  +  call_tool(fetch_url)")
    print("adapt_content_to_genscript: none (prompt adapter)")
    print("GenScriptAgent: execute_code (sandbox only)")
    print("save_scripts: none (deterministic host file write)")


def _print_agent_tool_status(state: dict[str, Any]) -> None:
    """Print configured tool status using colors: green=executed, red=not executed."""
    print("=== Agent Tool Status ===")
    counts: dict[str, dict[str, int]] = state.get("tool_call_counts", {})
    for agent_name, tools in AGENT_TOOL_CONFIG.items():
        if not tools:
            print(f"{agent_name}: none")
            continue
        rendered: list[str] = []
        for tool in tools:
            seen = counts.get(agent_name, {}).get(tool, 0)
            label = f"{tool}={seen}"
            rendered.append(_green(label) if seen > 0 else _red(label))
        print(f"{agent_name}: " + ", ".join(rendered))


def _extract_tool_name(data: Any) -> str | None:
    """Best-effort extraction for tool call/update event payloads."""
    candidates = (
        getattr(data, "tool_name", None),
        getattr(data, "name", None),
        getattr(data, "tool", None),
        getattr(data, "id", None),
    )
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def run_pipeline(target_date: date, output_dir: Path | None) -> None:
    runtime = HyperlightRuntime(output_dir=output_dir) if output_dir else HyperlightRuntime()
    runtime.init()
    try:
        state: dict[str, Any] = {"last_author": None, "tool_call_counts": {}}
        workflow = build_pipeline(runtime, target_date, state)
        print(f"\n=== Episode date: {target_date.isoformat()} ===")
        _print_agent_tool_inventory()

        outputs: list[str] = []

        # Stream every workflow event so the console reflects live progress.
        # See: control-flow/sequential_streaming.py and agents/azure_chat_agents_streaming.py
        stream = workflow.run(target_date.isoformat(), stream=True)
        async for event in stream:
            etype = getattr(event, "type", "")

            if etype == "executor_invoked":
                if state["last_author"] is not None:
                    print()
                    state["last_author"] = None
                print(f"\n--- [{event.executor_id}] invoked ---", flush=True)

            elif etype == "executor_completed":
                if state["last_author"] is not None:
                    print()
                    state["last_author"] = None
                print(f"--- [{event.executor_id}] completed ---", flush=True)

            elif etype == "agent_run_update":
                _print_agent_update(event, state)

            elif "tool" in etype.lower():
                if state["last_author"] is not None:
                    print()
                    state["last_author"] = None
                tool_name = _extract_tool_name(getattr(event, "data", None)) or "unknown_tool"
                owner = getattr(event, "executor_id", None) or "unknown_executor"
                print(_green(f"--- [{owner}] tool_event: {etype} -> {tool_name} ---"), flush=True)

            elif etype == "output":
                # Terminal output yielded by `Finalize.yield_output(...)`,
                # plus any AgentResponseUpdate the framework surfaces for
                # streaming agent nodes.
                data = event.data
                if hasattr(data, "text") and hasattr(data, "author_name"):
                    _print_agent_update(event, state)
                else:
                    if state["last_author"] is not None:
                        print()
                        state["last_author"] = None
                    outputs.append(str(data))

        result = None
        if hasattr(stream, "get_final_response"):
            result = await stream.get_final_response()

        print("\n\n=== Workflow complete ===")
        _print_agent_tool_status(state)
        print("=== Agent Tool Calls (observed in stream text) ===")
        tool_call_counts = state.get("tool_call_counts", {})
        if tool_call_counts:
            for agent_name, counts in tool_call_counts.items():
                pairs = ", ".join(f"{k}={v}" for k, v in counts.items())
                print(_green(f"{agent_name}: {pairs}"))
        else:
            print(_red("No tool call markers observed."))
        if result is not None:
            print(f"Final state: {result.get_final_state()}")
        for o in outputs:
            print(o)
    finally:
        runtime.shutdown()


def main() -> int:
    # Windows terminals often default to cp1252/cp936; force UTF-8 so streamed
    # multilingual agent updates never crash with UnicodeEncodeError.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    load_dotenv()
    args = parse_args()
    target_date = args.date or date.today()
    asyncio.run(run_pipeline(target_date=target_date, output_dir=args.output_dir))
    # HyperlightCodeActProvider holds per-run WasmSandbox handles whose Rust
    # drop is not Send; letting the interpreter's atexit cleanup run them on
    # the wrong thread produces noisy "WasmSandbox is unsendable" / temp-dir
    # PermissionError tracebacks on Windows. The episode is already on disk,
    # so bypass atexit and exit cleanly.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
