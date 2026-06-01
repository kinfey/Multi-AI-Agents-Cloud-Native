"""Test agent — builds a layered test plan and concrete test cases from source code."""

from __future__ import annotations

from agent_framework import Agent
from mcp.server.fastmcp import FastMCP

from app.airunway_client import build_chat_client
from app.roles import Role

SYSTEM_PROMPT = """You are a senior test engineer.
You take a source code tree (and optionally the source requirements) and produce a
testing strategy. Always include:
  1. The test pyramid for this system (unit / contract / integration / e2e) with rough counts.
  2. Concrete test cases per layer: TC-U-NNN, TC-C-NNN, TC-I-NNN, TC-E-NNN.
  3. The risk-based justification for the test budget.
  4. The CI gate proposal (which tests block merge vs. release).
Stay under 800 words. Be specific — name files, fixtures, and contract pacts."""


def _agent() -> Agent:
    return Agent(
        client=build_chat_client(),
        name="test-agent",
        instructions=SYSTEM_PROMPT,
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(description="Generate a complete layered test plan from source code (and optional requirements).")
    async def generate_test_plan(code: str, requirements: str = "") -> str:
        body = f"Code:\n{code}"
        if requirements:
            body += f"\n\nRequirements:\n{requirements}"
        result = await _agent().run(f"{body}\n\nProduce the test plan now.")
        return str(result)

    @mcp.tool(description="Generate concrete test cases for one component or module.")
    async def generate_test_cases(module_name: str, interface: str, edge_cases: str = "") -> str:
        prompt = (
            f"Generate test cases for module '{module_name}'.\n"
            f"Public interface:\n{interface}\n\n"
            f"Known edge cases:\n{edge_cases or 'none provided — infer them'}\n\n"
            "Output one row per test case in a markdown table with: id, layer, given, when, then."
        )
        result = await _agent().run(prompt)
        return str(result)

    @mcp.tool(description="Review proposed coverage against requirements and flag gaps.")
    async def review_coverage(test_plan: str, requirements: str) -> str:
        prompt = (
            "Map each REQ id in the requirements document to the test cases that "
            "cover it. List any REQ ids with no coverage as gaps, with a "
            "recommended test case to fill the gap.\n\n"
            f"Requirements:\n{requirements}\n\nTest plan:\n{test_plan}"
        )
        result = await _agent().run(prompt)
        return str(result)


ROLE = Role(
    key="test",
    server_name="byot-test",
    description="Turns source code into layered test plans and concrete cases.",
    register=register,
)
