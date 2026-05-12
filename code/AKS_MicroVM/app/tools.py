"""Function tools exposed to the GitHub Copilot SDK agent.

Keep tools small, side-effect free where possible, and never shell out
to commands that depend on the host. Anything risky must rely on the
agent's permission flow (handled in agent.py).
"""

from __future__ import annotations

import os
import platform
from datetime import datetime, timezone
from typing import Annotated

from agent_framework import tool
from pydantic import Field


@tool(approval_mode="never_require")
def get_pod_info() -> dict[str, str]:
    """Return information about the sandboxed runtime the agent is running in."""
    return {
        "hostname": platform.node(),
        "kernel": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "pod_name": os.environ.get("POD_NAME", "unknown"),
        "node_name": os.environ.get("NODE_NAME", "unknown"),
        "runtime_class": os.environ.get("RUNTIME_CLASS", "unknown"),
        "now_utc": datetime.now(timezone.utc).isoformat(),
    }


@tool(approval_mode="never_require")
def echo(
    message: Annotated[str, Field(description="Text to echo back to the caller.")],
) -> str:
    """Echo a message. Useful for smoke-testing the agent end-to-end."""
    return message
