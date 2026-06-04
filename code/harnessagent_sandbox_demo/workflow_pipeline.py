"""Graph-based workflow for the FIFA World Cup 2026 podcast pipeline.

Topology (built with `WorkflowBuilder` from `agent_framework`):

    prepare_search_prompt           ── emits AgentExecutorRequest ──
            │
            ▼
        SearchAgent                 ── harness agent (FoundryChatClient) ──
            │  AgentExecutorResponse
            ▼
    adapt_search_to_content         ── emits AgentExecutorRequest ──
            │
            ▼
        ContentAgent
            │
            ▼
    adapt_content_to_genscript
            │
            ▼
        GenScriptAgent
            │  AgentExecutorResponse
            ▼
        save_scripts                ── deterministic; writes via sandbox /output ──
                                    ── yields workflow output ──

Design notes:
- The spec calls for "三个 Agent". The three LLM agents are SearchAgent,
  ContentAgent, GenScriptAgent. Saving is a deterministic step — given
  (zh_cn, zh_tw, date), write two files — so we run it as a plain
  `Executor`. This avoids LLM safety refusals on the "exec this code that
  writes my JSON payload" prompt shape, and the files still flow through
  Hyperlight: we call `runtime.execute_code_async(...)` which writes into
  the guest's `/output` mount (mapped to the host's `./outputs` dir).
- Every adapter that feeds an `AgentExecutor` sends an
  `AgentExecutorRequest` with a single fresh `user` message, so the
  downstream `AgentExecutor.run` handler is invoked instead of `from_str`
  (which would lose context and log the "from_str ... empty cache" warning).

References:
- `_start-here/step2_agents_in_a_workflow.py`
- `_start-here/step3_streaming.py`
- `control-flow/sequential_executors.py`
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import Any

from agent_framework import (
    AgentExecutorRequest,
    AgentExecutorResponse,
    Executor,
    Message,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    handler,
)

from agents import (
    build_content_agent,
    build_genscript_agent,
    build_search_agent,
)
from sandbox import HyperlightRuntime


# ----------------------------- helpers ------------------------------------

_DATESTAMP_RE = re.compile(r"^\d{6}$")


def _datestamp(target_date: date | None) -> str:
    target = target_date or date.today()
    stamp = target.strftime("%y%m%d")
    if not _DATESTAMP_RE.match(stamp):
        raise ValueError(f"Bad date stamp: {stamp!r}")
    return stamp


def _split_scripts(genscript_output: str) -> tuple[str, str]:
    """Extract the two fenced blocks (```zh-CN ... ``` and ```zh-TW ... ```)."""
    blocks: dict[str, str] = {}
    for tag in ("zh-CN", "zh-TW"):
        pattern = re.compile(rf"```{tag}\s*\n(.*?)```", re.DOTALL)
        match = pattern.search(genscript_output)
        if not match:
            raise ValueError(f"GenScript output is missing the ```{tag} fenced block.")
        blocks[tag] = match.group(1).strip()
    return blocks["zh-CN"], blocks["zh-TW"]


def _response_text(payload: Any) -> str:
    """Extract text from an upstream `AgentExecutor`'s response.

    Inside a workflow, an `AgentExecutor` node forwards an
    `AgentExecutorResponse` to the next executor; we unwrap defensively in
    case the framework hands us a `str` or a raw `AgentResponse`.
    """
    if isinstance(payload, AgentExecutorResponse):
        text = payload.agent_response.text
        if isinstance(text, str) and text:
            return text
    if isinstance(payload, str):
        return payload
    text = getattr(payload, "text", None)
    if isinstance(text, str) and text:
        return text
    return str(payload)


def _user_request(prompt: str) -> AgentExecutorRequest:
    """Wrap a prompt as a fresh single-message `AgentExecutorRequest`.

    Each agent in the pipeline is intentionally context-isolated: the
    adapter has already embedded everything the next agent needs in
    `prompt`, so we send a clean `user` message rather than forwarding
    the upstream conversation.
    """
    return AgentExecutorRequest(
        messages=[Message("user", [prompt])],
        should_respond=True,
    )


# ----------------------------- executors ----------------------------------

class PrepareSearchPrompt(Executor):
    """Starts the workflow: turn the target date into the SearchAgent prompt."""

    def __init__(self, target_date: date) -> None:
        super().__init__(id="prepare_search_prompt")
        self._date = target_date

    @handler
    async def run(
        self,
        _input: str,
        ctx: WorkflowContext[AgentExecutorRequest],
    ) -> None:
        prompt = (
            f"Build the podcast for {self._date.isoformat()}. "
            "Follow your instructions: fetch "
            "https://www.bbc.com/sport/football/world-cup via "
            "`call_tool(\"fetch_url\", url=...)`, parse the LINKS section, "
            "verify each candidate, and return the final JSON block."
        )
        await ctx.send_message(_user_request(prompt))


def _normalize_host_lines(script: str, *, variant: str) -> str:
    """Normalize script text into required host-line format.

    Final persisted format:
      host : ...

      host : ...
    """
    normalized = script.replace("\r\n", "\n").replace("\r", "\n")
    out_lines: list[str] = []
    for raw in normalized.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("```"):
            continue
        line = re.sub(r"^#+\s*", "", line)
        line = re.sub(r"^\[(.*?)\]$", r"\1", line)
        line = re.sub(r"^(主持人|主持人：|host|Host)\s*[:：]\s*", "", line)

        # User requirement: final script must not mention BBC at all.
        replacement = "消息面" if variant == "zh-CN" else "外電消息"
        line = re.sub(r"\bBBC\s+Sport\b", replacement, line, flags=re.IGNORECASE)
        line = re.sub(r"\bBBC\b", replacement, line, flags=re.IGNORECASE)

        # Turn long paragraphs into radio-friendly beats.
        segments = [
            seg.strip()
            for seg in re.split(r"(?<=[。！？!?；;])\s*", line)
            if seg.strip()
        ]
        if not segments:
            continue
        for seg in segments:
            out_lines.append(f"host : {seg}")

    if not out_lines:
        return "host : （空白腳本）"

    # Keep pacing tight: merge tiny tail fragments into previous lines.
    merged: list[str] = []
    for line in out_lines:
        content = line[len("host : ") :]
        if merged and len(content) < 10:
            merged[-1] = merged[-1] + content
        else:
            merged.append(line)

    out_lines = merged

    # Keep final output in a reasonable host-line range.
    if len(out_lines) > 28:
        out_lines = out_lines[:28]

    return "\n\n".join(out_lines)


def _to_hk_cantonese(script: str) -> str:
    """Apply lightweight zh-TW -> Hong Kong Cantonese broadcast wording.

    This is a deterministic lexical pass to reduce Mandarin-style phrasing.
    """
    replacements: tuple[tuple[str, str], ...] = (
        ("我們", "我哋"),
        ("你們", "你哋"),
        ("你自己", "你哋自己"),
        ("這個", "呢個"),
        ("這屆", "今屆"),
        ("這次", "今次"),
        ("這裡", "呢度"),
        ("這一場", "呢一場"),
        ("比賽", "賽事"),
        ("進球", "入波"),
        ("進攻", "攻勢"),
        ("防守", "防線"),
        ("然後", "跟住"),
        ("最後", "臨尾"),
        ("接下來", "跟住落嚟"),
        ("現在", "而家"),
        ("非常", "好"),
        ("觀眾朋友", "球迷朋友"),
        ("明天", "聽日"),
        ("本集", "今集"),
        ("這集", "今集"),
        ("下期", "下集"),
        ("到這裡", "到呢度"),
        ("歡迎大家關注", "記得關注"),
    )

    out = script
    for old, new in replacements:
        out = out.replace(old, new)

    # Keep football lexicon consistent in HK usage.
    out = re.sub(r"世界盃\s*比數", "世界盃賽果", out)
    out = re.sub(r"足球比賽", "足球賽事", out)
    return out


def _ensure_required_closing(script: str, *, variant: str) -> str:
    """Ensure the script ends with goodbye + follow CTA for 《FIFA 2026 世界杯 5 分钟》."""
    closing_markers = (
        ("再见", "FIFA 2026 世界杯 5 分钟") if variant == "zh-CN" else ("再見", "FIFA 2026 世界盃 5 分鐘")
    )
    has_goodbye = closing_markers[0] in script
    has_cta = closing_markers[1] in script
    if has_goodbye and has_cta:
        return script

    required_line = (
        "host : 好了，今天就聊到这里，我们下期再见，也欢迎大家关注《FIFA 2026 世界杯 5 分钟》。"
        if variant == "zh-CN"
        else "host : 好，今集先到呢度，我哋下集再見，記得關注《世界盃 5 分鐘》。"
    )
    cleaned = script.rstrip()
    if not cleaned:
        return required_line
    return cleaned + "\n\n" + required_line


class AdaptSearchToContent(Executor):
    def __init__(self) -> None:
        super().__init__(id="adapt_search_to_content")

    @handler
    async def run(
        self,
        payload: Any,
        ctx: WorkflowContext[AgentExecutorRequest],
    ) -> None:
        text = _response_text(payload)
        prompt = (
            "Here is the SearchAgent output (JSON block at the end). "
            "Build the outline + DeepSearch brief per your instructions.\n\n"
            f"{text}"
        )
        await ctx.send_message(_user_request(prompt))


class AdaptContentToGenScript(Executor):
    def __init__(self) -> None:
        super().__init__(id="adapt_content_to_genscript")

    @handler
    async def run(
        self,
        payload: AgentExecutorResponse,
        ctx: WorkflowContext[AgentExecutorRequest],
    ) -> None:
        text = _response_text(payload)
        prompt = (
            "Here is the ContentAgent brief (outline + DeepSearch). "
            "Produce both the zh-CN and zh-TW scripts per your instructions.\n\n"
            f"{text}"
        )
        await ctx.send_message(_user_request(prompt))


class SaveScripts(Executor):
    """Deterministic save step — writes both scripts directly to the host.

    Why not run this through the sandbox? Two reasons:
    1. The guest's filesystem is WASI-style: only the preopened `/output`
       directory exists, and `os.makedirs('/output/<stamp>')` walks up to
       check `/` (which is NOT preopened) and fails with
       `FileNotFoundError: [Errno 44] No such file or directory: '/'`.
    2. The save step is genuinely not a security boundary — we are writing
       strings that this process just generated. The Hyperlight sandbox is
       used (correctly) for the *agent* steps that fetch arbitrary URLs and
       run model-generated code; using it again here adds no isolation, only
       a fragile WASI round-trip.

    The host output directory is the same one mounted into the sandbox as
    `/output`, so the on-disk layout is identical to the spec.
    """

    def __init__(self, target_date: date, runtime: HyperlightRuntime) -> None:
        super().__init__(id="save_scripts")
        self._date = target_date
        self._runtime = runtime

    @handler
    async def run(
        self,
        payload: AgentExecutorResponse,
        ctx: WorkflowContext[Any, str],
    ) -> None:
        text = _response_text(payload)
        try:
            zh_cn, zh_tw = _split_scripts(text)
        except ValueError as exc:
            # GenScript produced something other than the two fenced blocks.
            # Don't kill the workflow — save the raw output to both files so
            # the failure is visible on disk instead of as a crash.
            print(f"[save_scripts] WARNING: {exc} — saving raw output to both files.")
            zh_cn = zh_tw = text

        zh_cn = _normalize_host_lines(zh_cn, variant="zh-CN")
        zh_tw = _normalize_host_lines(zh_tw, variant="zh-TW")
        zh_cn = _ensure_required_closing(zh_cn, variant="zh-CN")
        zh_tw = _ensure_required_closing(zh_tw, variant="zh-TW")
        zh_tw = _to_hk_cantonese(zh_tw)

        stamp = _datestamp(self._date)
        episode_dir = (self._runtime.output_dir / stamp).resolve()
        episode_dir.mkdir(parents=True, exist_ok=True)
        zh_cn_path = episode_dir / f"{stamp}.simple.zh.txt"
        zh_tw_path = episode_dir / f"{stamp}.tranditional.zh.txt"
        zh_cn_path.write_text(zh_cn, encoding="utf-8")
        zh_tw_path.write_text(zh_tw, encoding="utf-8")

        blob_lines = _upload_to_blob(stamp, [zh_cn_path, zh_tw_path])

        summary = (
            f"Saved episode {stamp} to {episode_dir}\n"
            f"  - {zh_cn_path.name}  ({len(zh_cn):,} chars)\n"
            f"  - {zh_tw_path.name}  ({len(zh_tw):,} chars)"
            + ("\n" + "\n".join(blob_lines) if blob_lines else "")
        )
        print(summary)
        await ctx.yield_output(summary)


def _upload_to_blob(stamp: str, paths: list[Path]) -> list[str]:
    """Upload generated scripts to Azure Blob Storage, if configured.

    Controlled by AZURE_STORAGE_ACCOUNT + AZURE_STORAGE_CONTAINER. Uses
    DefaultAzureCredential so it picks up the workload-identity token in
    AKS or `az login` locally.
    """
    account = os.environ.get("AZURE_STORAGE_ACCOUNT", "").strip()
    container = os.environ.get("AZURE_STORAGE_CONTAINER", "").strip()
    if not account or not container:
        return []
    try:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient
    except ImportError as exc:
        print(f"[save_scripts] blob upload skipped: {exc}")
        return []

    account_url = f"https://{account}.blob.core.windows.net"
    try:
        client = BlobServiceClient(account_url=account_url, credential=DefaultAzureCredential())
        container_client = client.get_container_client(container)
        lines: list[str] = []
        for p in paths:
            blob_name = f"{stamp}/{p.name}"
            with p.open("rb") as fh:
                container_client.upload_blob(name=blob_name, data=fh, overwrite=True)
            lines.append(f"  - uploaded {account_url}/{container}/{blob_name}")
        return lines
    except Exception as exc:  # noqa: BLE001 — never fail the run on upload errors
        print(f"[save_scripts] blob upload failed: {exc}")
        return [f"  - blob upload failed: {exc}"]


# ----------------------------- builder ------------------------------------

def build_pipeline(
    runtime: HyperlightRuntime,
    target_date: date,
    state: dict[str, Any],
) -> Workflow:
    """Build a fresh `Workflow` for the given episode date.

    `state` is the shared dict that each agent's tool-call recorder
    middleware writes into. main.py owns it so the final per-agent
    tool-status table can be rendered.

    Following the `create_workflow()` state-isolation pattern from
    `_start-here/step1_executors_and_edges.py`: every call returns a new
    graph with new executor / agent instances so runs cannot share state.
    """
    prepare = PrepareSearchPrompt(target_date)
    search_agent = build_search_agent(runtime, target_date, state)
    adapt_search = AdaptSearchToContent()
    content_agent = build_content_agent(runtime, target_date, state)
    adapt_content = AdaptContentToGenScript()
    genscript_agent = build_genscript_agent(runtime, target_date, state)
    save = SaveScripts(target_date, runtime)

    return (
        WorkflowBuilder(start_executor=prepare)
        .add_edge(prepare, search_agent)
        .add_edge(search_agent, adapt_search)
        .add_edge(adapt_search, content_agent)
        .add_edge(content_agent, adapt_content)
        .add_edge(adapt_content, genscript_agent)
        .add_edge(genscript_agent, save)
        .build()
    )


