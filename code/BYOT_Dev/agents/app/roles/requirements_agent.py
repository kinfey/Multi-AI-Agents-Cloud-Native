"""Requirements agent — turns an idea into a structured requirements document."""

from __future__ import annotations

from agent_framework import Agent
from mcp.server.fastmcp import FastMCP

from app.airunway_client import build_chat_client
from app.roles import Role

SYSTEM_PROMPT = """You are a senior product analyst.
Your job is to translate a rough product idea into a precise requirements
document. Always produce:
  1. A one-sentence problem statement.
  2. A bulleted list of functional requirements numbered REQ-F-001 ... REQ-F-NNN.
  3. A bulleted list of non-functional requirements numbered REQ-N-001 ... REQ-N-NNN.
  4. A list of open questions that block implementation.
Keep the document under 500 words. Be specific, not generic."""


def _agent() -> Agent:
    return Agent(
        client=build_chat_client(),
        name="requirements-agent",
        instructions=SYSTEM_PROMPT,
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(description="Turn a raw product idea into a structured requirements document.")
    async def gather_requirements(idea: str) -> str:
        result = await _agent().run(
            f"Idea:\n{idea}\n\nProduce the requirements document now."
        )
        return str(result)

    @mcp.tool(description="Refine a single requirement that the team finds ambiguous.")
    async def clarify_requirement(requirement: str, context: str = "") -> str:
        prompt = (
            "Rewrite the following requirement so it is testable, atomic, and "
            "unambiguous. Keep the same REQ id if present.\n\n"
            f"Requirement: {requirement}\n\nContext:\n{context}"
        )
        result = await _agent().run(prompt)
        return str(result)

    @mcp.tool(description="Produce a final, signed-off requirements document from accumulated notes.")
    async def produce_requirements_doc(notes: str) -> str:
        prompt = (
            "Consolidate the following notes into the final requirements "
            "document, deduplicating and renumbering REQ ids consistently.\n\n"
            f"Notes:\n{notes}"
        )
        result = await _agent().run(prompt)
        return str(result)


ROLE = Role(
    key="requirements",
    server_name="byot-requirements",
    description="Turns ideas into structured requirements docs.",
    register=register,
)
