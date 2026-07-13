from typing import Protocol

from .models import AgentResult, WorkflowStep


class AgentTransport(Protocol):
    def run_agent(self, step: WorkflowStep, prompt: str) -> AgentResult:
        pass


class DeterministicLocalTransport:
    def run_agent(self, step: WorkflowStep, prompt: str) -> AgentResult:
        content = _local_agent_response(step, prompt)
        return AgentResult(step=step, prompt=prompt, content=content, metadata={"transport": "local"})


def _local_agent_response(step: WorkflowStep, prompt: str) -> str:
    if step == WorkflowStep.REQUIREMENTS:
        return (
            "# Requirement Agent Result\n\n"
            f"Requirement Summary: {prompt}\n\n"
            "Acceptance Criteria:\n"
            "- The requested behavior is stated as testable outcomes.\n"
            "- Ambiguities are captured before implementation.\n\n"
            "Review Notes:\n"
            "- Criteria are concrete enough for the coding agent to implement.\n"
        )
    if step == WorkflowStep.CODING:
        return (
            "# Coding Agent Result\n\n"
            "Implementation:\n"
            "- Produce minimal Python code that satisfies the accepted requirements.\n"
            "- Keep changes scoped to the requested behavior.\n\n"
            "Files Changed:\n"
            "- app/openclaw_workflow/workflow.py\n\n"
            "Review Notes:\n"
            "- Implementation references the accepted requirement artifact.\n"
        )
    if step == WorkflowStep.TESTING:
        return (
            "# Testing Agent Result\n\n"
            "Test Plan:\n"
            "- Verify agent ordering and review gate enforcement.\n"
            "- Verify rejected reviews stop the workflow.\n\n"
            "Test Evidence:\n"
            "- pytest app/tests\n\n"
            "Review Notes:\n"
            "- Tests cover the workflow control path.\n"
        )
    if step == WorkflowStep.DEPLOYMENT:
        return (
        "# Deployment Agent Result\n\n"
        "Deployment Plan:\n"
        "- Build OpenClaw image, push to ACR, deploy Container App, then run smoke checks.\n"
        "- Provision ACA Sandbox group for operator commands.\n\n"
        "Rollback:\n"
        "- Revert to the previous Container Apps revision or update image tag to the last known-good tag.\n\n"
        "Review Notes:\n"
        "- Deployment waits for validation before execution.\n"
        )
    return (
        "# Save Agent Result\n\n"
        "Archive:\n"
        "- Package the coding-agent and testing-agent workspaces.\n\n"
        "Download URL:\n"
        "- Returned by the authenticated artifact service.\n\n"
        "Review Notes:\n"
        "- The archive is created only after deployment succeeds.\n"
    )
