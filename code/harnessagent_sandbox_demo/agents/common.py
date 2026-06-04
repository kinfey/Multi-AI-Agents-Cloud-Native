"""Shared helpers for building harness-style agents over the Foundry chat client."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Sequence

from agent_framework import (
    FunctionInvocationContext,
    SkillsProvider,
    create_harness_agent,
    function_middleware,
)
from agent_framework.foundry import FoundryChatClient
from azure.identity import AzureCliCredential, DefaultAzureCredential


# Repository-root `skills/` folder. Each subdirectory is one SKILL.md package.
SKILLS_ROOT = Path(__file__).resolve().parent.parent / "skills"


def skill_path(*names: str) -> list[str]:
    """Resolve one or more skill folders under `skills/` to absolute paths.

    Each name is the directory containing a SKILL.md file (e.g.
    "search-bbc-worldcup"). Pass the result to `create_harness_agent`'s
    `skills_paths=` so the harness's built-in SkillsProvider discovers them.
    """
    return [str(SKILLS_ROOT / name) for name in names]


def build_foundry_client() -> FoundryChatClient:
    """FoundryChatClient bound to the Foundry project endpoint in .env.

    Credential source is selected by `AZURE_CREDENTIAL_KIND`:
      * "default" (default) — `DefaultAzureCredential`. Works for local
        `az login` *and* for Kubernetes Workload Identity (federated
        token in the pod). Recommended for in-cluster runs.
      * "cli" — `AzureCliCredential`. Forces local `az login` only.
    """
    kind = os.environ.get("AZURE_CREDENTIAL_KIND", "default").lower()
    if kind == "cli":
        credential: Any = AzureCliCredential()
    else:
        credential = DefaultAzureCredential()
    return FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ["FOUNDRY_MODEL"],
        credential=credential,
    )


def make_tool_call_recorder(state: dict[str, Any], agent_name: str):
    """Build a function-middleware that records every tool invocation.

    Captures both the model-visible `execute_code` calls and the inner
    `call_tool("name", ...)` invocations the CodeAct provider routes back
    to host-registered tools — both flow through the same FunctionTool
    pipeline, so a single middleware sees them all.
    """

    @function_middleware
    async def _recorder(
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        name = getattr(context.function, "name", None) or "unknown_tool"
        counters = state.setdefault("tool_call_counts", {}).setdefault(agent_name, {})
        counters[name] = counters.get(name, 0) + 1
        await call_next()

    return _recorder


def make_call_tool_counter(state: dict[str, Any], agent_name: str, tool_name: str):
    """Build a zero-arg counter for tools invoked from inside the sandbox.

    Tools called from the guest via `call_tool(...)` bypass the agent's
    function middleware (Hyperlight's host dispatch awaits the FunctionTool
    directly). Pass the returned callable as `on_call=` on the tool factory
    so each guest-side invocation still increments
    `state["tool_call_counts"][agent_name][tool_name]`.
    """

    def _bump() -> None:
        counters = state.setdefault("tool_call_counts", {}).setdefault(agent_name, {})
        counters[tool_name] = counters.get(tool_name, 0) + 1

    return _bump


def build_harness(
    *,
    name: str,
    description: str,
    instructions: str,
    tools: Sequence[Any] | None = None,
    context_providers: Sequence[Any] | None = None,
    middleware: Sequence[Any] | None = None,
    skills_paths: Sequence[str] | None = None,
    max_context_window_tokens: int = 128_000,
    max_output_tokens: int = 16_384,
):
    """Wrap `create_harness_agent` with our standard defaults.

    The harness brings in todo / mode / compaction / skills / telemetry
    providers automatically. `skills_paths` (plural) is forwarded so the
    harness's built-in SkillsProvider auto-discovers SKILL.md packages.
    """
    client = build_foundry_client()
    # Each agent in this pipeline is a single-shot worker node. The harness's
    # plan/execute Mode and Todo providers turn it into an interactive planner
    # that waits for human approval, which deadlocks an autonomous workflow.
    # Web search is also off — every fetch goes through the CodeAct provider.
    kwargs: dict[str, Any] = dict(
        client=client,
        name=name,
        description=description,
        agent_instructions=instructions,
        max_context_window_tokens=max_context_window_tokens,
        max_output_tokens=max_output_tokens,
        disable_mode=True,
        disable_todo=True,
        disable_web_search=True,
    )
    if tools:
        kwargs["tools"] = list(tools)
    if context_providers:
        kwargs["context_providers"] = list(context_providers)
    if middleware:
        kwargs["middleware"] = list(middleware)
    if skills_paths:
        # NOTE: do not pass `skills_paths=` to create_harness_agent — the
        # harness unpacks the list with `*skills_paths` into
        # `SkillsProvider.from_paths`, which only accepts a single sequence
        # argument and crashes when more than one path is given. Build the
        # provider ourselves and pass it via `skills_provider=`.
        kwargs["skills_provider"] = SkillsProvider.from_paths(list(skills_paths))
    return create_harness_agent(**kwargs)

