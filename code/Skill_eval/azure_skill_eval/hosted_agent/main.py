"""Foundry Hosted Agent (Responses protocol).

This follows the Agent Framework hosted-agent responses samples: Foundry runs
this container as a hosted agent, and the agent exposes the Responses protocol
through ``ResponsesHostServer``. The edu-video-script skill is packaged with the
agent source and loaded through ``SkillsProvider.from_paths`` at startup.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from azure.identity.aio import DefaultAzureCredential

AGENT_DIR = Path(__file__).resolve().parent
# Support both layouts:
#   repo root: hosted_agent/main.py + sibling ../shared
#   azd agent src: main.py + sibling ./shared
sys.path.insert(0, str(AGENT_DIR))
sys.path.insert(0, str(AGENT_DIR.parent))

from shared.business_agent import BUSINESS_INSTRUCTIONS, make_business_skill  # noqa: E402

SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def _env_any(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _project_endpoint() -> str:
    value = _env_any(
        "FOUNDRY_PROJECT_ENDPOINT",
        "AZURE_AI_PROJECT_ENDPOINT",
        "AZURE_AIPROJECT_ENDPOINT",
        "PROJECT_ENDPOINT",
    )
    if not value:
        raise RuntimeError("Foundry project endpoint was not provided by the platform.")
    return value


def _build_agent(credential):
    from agent_framework import Agent, SkillsProvider
    from agent_framework.foundry import FoundryChatClient

    model = _env_any("AZURE_AI_MODEL_DEPLOYMENT_NAME", "MODEL_GPT", default="gpt-5.5")
    client = FoundryChatClient(
        project_endpoint=_project_endpoint(),
        model=model,
        credential=credential,
    )
    return Agent(
        client=client,
        name=_env_any("AGENT_NAME", "AZURE_AI_AGENT_NAME", default="skill-eval-business-agent"),
        instructions=BUSINESS_INSTRUCTIONS,
        context_providers=[SkillsProvider(make_business_skill())],
        default_options={"store": False},
    )


async def serve() -> None:
    from agent_framework_foundry_hosting import ResponsesHostServer

    async with DefaultAzureCredential() as credential:
        agent = _build_agent(credential)
        server = ResponsesHostServer(agent)
        await server.run_async()


def main() -> None:
    import asyncio
    asyncio.run(serve())


if __name__ == "__main__":
    main()
