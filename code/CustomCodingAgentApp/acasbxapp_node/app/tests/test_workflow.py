import unittest

from openclaw_workflow.models import AgentResult, WorkflowStep
from openclaw_workflow.review import RuleBasedReviewer
from openclaw_workflow.workflow import ProgrammingWorkflow, ReviewGateError


class RecordingTransport:
    def __init__(self) -> None:
        self.steps: list[WorkflowStep] = []

    def run_agent(self, step: WorkflowStep, prompt: str) -> AgentResult:
        self.steps.append(step)
        sections = {
            WorkflowStep.REQUIREMENTS: "Acceptance Criteria:\n- Done\n\nReview Notes:\n- Passed",
            WorkflowStep.CODING: "Implementation:\n- Done\n\nFiles Changed:\n- app.py\n\nReview Notes:\n- Passed",
            WorkflowStep.TESTING: "Test Plan:\n- Run tests\n\nTest Evidence:\n- Passed\n\nReview Notes:\n- Passed",
            WorkflowStep.DEPLOYMENT: (
                "Deployment Plan:\n- Deploy\n\nAzure Context:\n- Azure Container Apps"
                "\n\nDeployment Result:\n- {\"status\":\"succeeded\"}"
                "\n\nApplication URL:\n- https://app.example.test"
                "\n\nRollback:\n- Revert\n\nReview Notes:\n- Passed"
            ),
            WorkflowStep.SAVE: "Archive:\n- project.zip\n\nDownload URL:\n- https://example.test/project.zip\n\nReview Notes:\n- Passed",
        }
        return AgentResult(step=step, prompt=prompt, content=sections[step])


class FailingCodingTransport(RecordingTransport):
    def run_agent(self, step: WorkflowStep, prompt: str) -> AgentResult:
        if step == WorkflowStep.CODING:
            self.steps.append(step)
            return AgentResult(step=step, prompt=prompt, content="Implementation:\n[BLOCKED]")
        return super().run_agent(step, prompt)


class ProgrammingWorkflowTests(unittest.TestCase):
    def test_workflow_runs_agents_in_required_order(self) -> None:
        transport = RecordingTransport()
        report = ProgrammingWorkflow(transport=transport).run("Build a CLI calculator")

        self.assertTrue(report.approved)
        self.assertEqual(
            transport.steps,
            [
                WorkflowStep.REQUIREMENTS,
                WorkflowStep.CODING,
                WorkflowStep.TESTING,
                WorkflowStep.DEPLOYMENT,
                WorkflowStep.SAVE,
            ],
        )

    def test_review_gate_stops_before_next_agent_on_failure(self) -> None:
        transport = FailingCodingTransport()

        with self.assertRaises(ReviewGateError):
            ProgrammingWorkflow(transport=transport).run("Build a CLI calculator")

        self.assertEqual(transport.steps, [WorkflowStep.REQUIREMENTS, WorkflowStep.CODING])

    def test_deployment_requires_success_status_and_https_url(self) -> None:
        result = AgentResult(
            step=WorkflowStep.DEPLOYMENT,
            prompt="Deploy",
            content=(
                "Deployment Plan:\n- Deploy\n\nAzure Context:\n- ACA"
                "\n\nDeployment Result:\n- Pending\n\nApplication URL:\n- Pending"
                "\n\nRollback:\n- Revert\n\nReview Notes:\n- Checked"
            ),
        )

        decision = RuleBasedReviewer().review(result)

        self.assertFalse(decision.approved)
        self.assertIn("Deployment did not report a succeeded status.", decision.findings)
        self.assertIn("Deployment did not report an HTTPS application URL.", decision.findings)


if __name__ == "__main__":
    unittest.main()
