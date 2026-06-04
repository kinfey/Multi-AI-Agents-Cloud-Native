"""Content Agent — outline + DeepSearch enrichment.

Instructions live in two SKILL.md packages, loaded via the harness's
built-in SkillsProvider:

- `skills/hyperlight-sandbox/` — execute_code + call_tool("fetch_url", ...) rules
- `skills/content-deepsearch/` — outline + DeepSearch workflow
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sandbox import (
    HyperlightRuntime,
    build_codeact_provider,
    make_fetch_url_tool,
)

from .common import (
    build_harness,
    make_call_tool_counter,
    make_tool_call_recorder,
    skill_path,
)


CONTENT_INSTRUCTIONS = """\
You are ContentAgent. You receive a JSON list of the top 5 FIFA World
Cup 2026 stories from SearchAgent. Deepen them into a podcast-ready
research brief — do not re-discover stories.

Two skills are advertised to you. **You must load both before doing
anything else** (call `load_skill` for each):

1. `hyperlight-sandbox` — how to use `execute_code` and the
   `call_tool("fetch_url", ...)` host bridge.
2. `content-deepsearch` — the outline + DeepSearch workflow and the
   exact Markdown output shape.

Then follow the workflow in `content-deepsearch` exactly. Run
autonomously and return the final Markdown brief in this same turn.
"""


def build_content_agent(
    runtime: HyperlightRuntime,
    target_date: date,
    state: dict[str, Any],
):
    del target_date  # not currently parameterised here
    provider = build_codeact_provider(
        source_id="content_codeact",
        tools=[
            make_fetch_url_tool(
                runtime,
                on_call=make_call_tool_counter(state, "ContentAgent", "fetch_url"),
            ),
        ],
        workspace_root=runtime.output_dir,
    )
    return build_harness(
        name="ContentAgent",
        description="Deepens the SearchAgent's top-5 stories into a podcast outline + findings.",
        instructions=CONTENT_INSTRUCTIONS,
        context_providers=[provider],
        middleware=[make_tool_call_recorder(state, "ContentAgent")],
        skills_paths=skill_path("hyperlight-sandbox", "content-deepsearch"),
    )
