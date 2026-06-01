"""Code agent — turns requirements into idiomatic, ready-to-run source code."""

from __future__ import annotations

from agent_framework import Agent
from mcp.server.fastmcp import FastMCP

from app.airunway_client import build_chat_client
from app.roles import Role

SYSTEM_PROMPT = """You are a senior software engineer who writes idiomatic,
production-quality code.
Rules for every response:
  1. One fenced code block per file. Start each block with a comment line
     that gives the relative file path, e.g. `# app/main.py` or `// src/lib.rs`.
  2. Include type hints / static types and short docstrings for public symbols.
  3. Prefer the standard library; only pull in a third-party dependency if it
     buys real leverage, and list it explicitly.
  4. After the code, add a short "How to run" section (max 5 lines).
  5. Never invent unspecified behaviour — if a requirement is ambiguous, write
     a `TODO(clarify):` comment instead of guessing.
Keep total output under 900 words."""


def _agent() -> Agent:
    return Agent(
        client=build_chat_client(),
        name="code-agent",
        instructions=SYSTEM_PROMPT,
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(description="Implement a runnable project skeleton directly from a requirements document.")
    async def implement_from_requirements(
        requirements: str,
        language: str = "python",
        framework: str = "",
    ) -> str:
        prompt = (
            f"Implement the smallest runnable project that satisfies these requirements.\n"
            f"Language: {language}\n"
            f"Framework: {framework or 'pick a sensible default and state it'}\n\n"
            f"Requirements document:\n{requirements}\n\n"
            "Return one fenced code block per file plus the 'How to run' section."
        )
        result = await _agent().run(prompt)
        return str(result)

    @mcp.tool(description="Write source code for one module given its spec / interface.")
    async def write_module(
        module_spec: str,
        language: str = "python",
        framework: str = "",
    ) -> str:
        prompt = (
            f"Write the source code for the module described below.\n"
            f"Language: {language}\n"
            f"Framework: {framework or 'standard library only unless the spec demands otherwise'}\n\n"
            f"Spec:\n{module_spec}\n\n"
            "Return one fenced code block per file. Include unit-test-friendly seams "
            "(dependency injection, pure functions) where natural."
        )
        result = await _agent().run(prompt)
        return str(result)

    @mcp.tool(description="Refactor existing code toward a stated goal (readability, perf, testability, etc.).")
    async def refactor_code(code: str, goal: str) -> str:
        prompt = (
            f"Refactor the following code with this goal: {goal}\n\n"
            f"```\n{code}\n```\n\n"
            "Return the refactored code as one or more fenced blocks, then a short\n"
            "'Changes' bullet list explaining each material change and why."
        )
        result = await _agent().run(prompt)
        return str(result)

    @mcp.tool(description="Review code and return a structured critique with concrete fixes.")
    async def review_code(code: str) -> str:
        prompt = (
            "Review the following code as a staff engineer. Group findings under\n"
            "  - Correctness\n  - Security\n  - Performance\n  - Readability / API\n"
            "For each finding give: severity (info/warn/block), location, and a\n"
            "minimal patch as a fenced diff or replacement block.\n\n"
            f"```\n{code}\n```"
        )
        result = await _agent().run(prompt)
        return str(result)


ROLE = Role(
    key="code",
    server_name="byot-code",
    description="Writes, refactors, and reviews source code from requirements or module specs.",
    register=register,
)
