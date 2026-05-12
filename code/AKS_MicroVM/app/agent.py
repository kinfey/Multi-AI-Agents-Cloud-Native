"""GitHubCopilotAgent factory hardened for a Kata microVM sandbox.

The agent is intentionally constructed with a conservative permission policy:
all shell / file / URL / MCP permission requests are denied unless they appear
on a configurable allow-list. The microVM sandbox is the last line of defense,
not the first — defense in depth still applies inside the guest.
"""

from __future__ import annotations

import logging
import os
from typing import Final

from agent_framework.github import GitHubCopilotAgent
from copilot.generated.session_events import PermissionRequest
from copilot.session import PermissionRequestResult

from .tools import echo, get_pod_info

logger = logging.getLogger(__name__)

_DEFAULT_INSTRUCTIONS: Final[str] = (
    "You are a security-hardened coding assistant running inside a Kata "
    "microVM on AKS. Prefer pure reasoning. Only call tools that are "
    "explicitly registered. Never request shell/write permissions unless "
    "the user clearly asks for it."
)


def _parse_csv_env(name: str) -> set[str]:
    raw = os.environ.get(name, "").strip()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _build_permission_handler():
    """Build a permission handler that allow-lists kinds via env vars.

    `COPILOT_ALLOW_KINDS` is a comma-separated list of PermissionRequest.kind
    values to approve (e.g. `read,url`). Anything not listed is denied.
    """

    allowed_kinds = _parse_csv_env("COPILOT_ALLOW_KINDS")

    def on_permission_request(
        request: PermissionRequest, context: dict[str, str]
    ) -> PermissionRequestResult:
        if request.kind in allowed_kinds:
            logger.info(
                "permission approved: kind=%s path=%s", request.kind, getattr(request, "path", None)
            )
            return PermissionRequestResult(kind="approved")

        logger.warning(
            "permission denied: kind=%s path=%s (not in COPILOT_ALLOW_KINDS=%s)",
            request.kind,
            getattr(request, "path", None),
            sorted(allowed_kinds),
        )
        return PermissionRequestResult(kind="denied-interactively-by-user")

    return on_permission_request


def build_agent() -> GitHubCopilotAgent:
    """Create the GitHubCopilotAgent used by the FastAPI service."""

    model = os.environ.get("GITHUB_COPILOT_MODEL") or None
    timeout = int(os.environ.get("GITHUB_COPILOT_TIMEOUT", "120"))

    default_options: dict[str, object] = {
        "on_permission_request": _build_permission_handler(),
        "timeout": timeout,
    }
    if model:
        default_options["model"] = model

    return GitHubCopilotAgent(
        instructions=_DEFAULT_INSTRUCTIONS,
        tools=[get_pod_info, echo],
        default_options=default_options,
        name="kata-microvm-copilot-agent",
        description="GitHub Copilot SDK agent isolated by Kata microVM on AKS.",
    )
