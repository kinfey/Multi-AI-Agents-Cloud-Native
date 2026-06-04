"""Search Agent — top 5 FIFA World Cup 2026 stories of the day.

Instructions live in two SKILL.md packages, loaded via the harness's
built-in SkillsProvider:

- `skills/hyperlight-sandbox/` — execute_code + call_tool("fetch_url", ...) rules
- `skills/search-bbc-worldcup/` — the BBC-only search workflow
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


SEARCH_INSTRUCTIONS = """\
You are SearchAgent. Your only job is to surface the 5 most important
FIFA World Cup 2026 stories of the day from BBC Sport.

Two skills are advertised to you. **You must load both before doing
anything else** (call `load_skill` for each):

1. `hyperlight-sandbox` — how to use `execute_code` and the
   `call_tool("fetch_url", ...)` host bridge.
2. `search-bbc-worldcup` — the required workflow, source policy, URL
   verification rules, and the exact JSON output shape.

Then follow the workflow in `search-bbc-worldcup` exactly. Run
autonomously and return the final JSON in this same turn.
"""


def build_search_agent(
    runtime: HyperlightRuntime,
    target_date: date,
    state: dict[str, Any],
):
    del target_date  # the agent fetches inside the guest
    provider = build_codeact_provider(
        source_id="search_codeact",
        tools=[
            make_fetch_url_tool(
                runtime,
                on_call=make_call_tool_counter(state, "SearchAgent", "fetch_url"),
            ),
        ],
        workspace_root=runtime.output_dir,
    )
    return build_harness(
        name="SearchAgent",
        description="Finds the latest 5 World Cup stories from BBC Sport (BBC-only).",
        instructions=SEARCH_INSTRUCTIONS,
        context_providers=[provider],
        middleware=[make_tool_call_recorder(state, "SearchAgent")],
        skills_paths=skill_path("hyperlight-sandbox", "search-bbc-worldcup"),
    )
