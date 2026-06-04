"""GenScript Agent — turn the research brief into a *FIFA 2026 世界杯 5 分钟* podcast script.

Instructions live in two SKILL.md packages, loaded via the harness's
built-in SkillsProvider:

- `skills/hyperlight-sandbox/` — execute_code rules (no host bridges here)
- `skills/genscript-podcast/` — script style, output format, mandatory length check
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sandbox import (
    HyperlightRuntime,
    build_codeact_provider,
)

from .common import build_harness, make_tool_call_recorder, skill_path


GENSCRIPT_INSTRUCTIONS = """\
You are GenScriptAgent. You receive an outline + DeepSearch brief.
Write the on-air script for **《FIFA 2026 世界杯 5 分钟》**, hosted by
Kinfey Lo (only one host).

Two skills are advertised to you. **You must load both before doing
anything else** (call `load_skill` for each):

1. `hyperlight-sandbox` — how to use `execute_code` (this agent has
   NO `call_tool` host bridges — no network).
2. `genscript-podcast` — style guide, exact two-fenced-block output
   format, and the MANDATORY in-sandbox length-verification step.

Then follow the workflow in `genscript-podcast` exactly. Run
autonomously and return the two fenced Markdown blocks in this same
turn. Do not ask the user to paste the brief again.
"""


def build_genscript_agent(
    runtime: HyperlightRuntime,
    target_date: date,
    state: dict[str, Any],
):
    del target_date  # host SaveScripts executor handles the date stamp
    provider = build_codeact_provider(
        source_id="genscript_codeact",
        tools=[],
        workspace_root=runtime.output_dir,
    )
    return build_harness(
        name="GenScriptAgent",
        description="Generates Simplified + Traditional Chinese podcast scripts for 《FIFA 2026 世界杯 5 分钟》.",
        instructions=GENSCRIPT_INSTRUCTIONS,
        context_providers=[provider],
        middleware=[make_tool_call_recorder(state, "GenScriptAgent")],
        skills_paths=skill_path("hyperlight-sandbox", "genscript-podcast"),
    )
