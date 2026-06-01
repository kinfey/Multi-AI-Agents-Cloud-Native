"""Role registry — selects the agent role from the AGENT_ROLE env var."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from mcp.server.fastmcp import FastMCP

RoleRegistrar = Callable[[FastMCP], None]


@dataclass(frozen=True)
class Role:
    key: str
    server_name: str
    description: str
    register: RoleRegistrar


def _load(name: str) -> Role:
    if name == "requirements":
        from . import requirements_agent
        return requirements_agent.ROLE
    if name == "code":
        from . import code_agent
        return code_agent.ROLE
    if name == "test":
        from . import test_agent
        return test_agent.ROLE
    if name == "deploy":
        from . import deploy_agent
        return deploy_agent.ROLE
    raise ValueError(
        f"Unknown AGENT_ROLE={name!r}. Expected one of: requirements, code, test, deploy."
    )


def build_role(name: str) -> Role:
    return _load(name)
