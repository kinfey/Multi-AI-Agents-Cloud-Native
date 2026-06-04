"""Hyperlight CodeAct provider helpers.

Mirrors the pattern from
`python/samples/02-agents/context_providers/code_act/code_act.py`:

    codeact = HyperlightCodeActProvider(tools=[...], approval_mode="never_require")
    agent  = Agent(..., context_providers=[codeact])

The model only sees `execute_code`. Every other tool registered on the
provider becomes callable from inside the sandbox via
`call_tool("name", arg=...)`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

from agent_framework_hyperlight import AllowedDomain, HyperlightCodeActProvider


# Allow-list shared by every CodeAct provider in the workflow. The pipeline's
# single source of truth is BBC Sport — no other domain is permitted.
_DEFAULT_ALLOWED_DOMAINS: tuple[AllowedDomain, ...] = (
    AllowedDomain("https://www.bbc.com", ("GET",)),
)


def build_codeact_provider(
    *,
    source_id: str,
    tools: Sequence[Any],
    workspace_root: Path | None = None,
) -> HyperlightCodeActProvider:
    """Build a HyperlightCodeActProvider with the podcast workflow defaults.

    Reads `HYPERLIGHT_PYTHON_MODULE_PATH` from the environment so every
    provider in the pipeline points at the same built guest module.
    """
    module_path = os.environ.get("HYPERLIGHT_PYTHON_MODULE_PATH") or None
    if module_path is None:
        raise RuntimeError(
            "HYPERLIGHT_PYTHON_MODULE_PATH is not set. Point it at your built "
            "python-sandbox.aot guest module before launching the workflow."
        )

    return HyperlightCodeActProvider(
        source_id,
        tools=list(tools),
        approval_mode="never_require",
        workspace_root=workspace_root,
        allowed_domains=_DEFAULT_ALLOWED_DOMAINS,
        module_path=module_path,
    )
