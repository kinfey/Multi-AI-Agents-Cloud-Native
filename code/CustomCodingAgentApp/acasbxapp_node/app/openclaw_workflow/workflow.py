from collections.abc import Iterable

from .models import AgentResult, ReviewDecision, WorkflowReport, WorkflowStep
from .review import RuleBasedReviewer
from .transports import AgentTransport, DeterministicLocalTransport


DEFAULT_STEPS: tuple[WorkflowStep, ...] = (
    WorkflowStep.REQUIREMENTS,
    WorkflowStep.CODING,
    WorkflowStep.TESTING,
    WorkflowStep.DEPLOYMENT,
    WorkflowStep.SAVE,
)


class ReviewGateError(RuntimeError):
    def __init__(self, decision: ReviewDecision) -> None:
        findings = "; ".join(decision.findings)
        super().__init__(f"Review gate failed for {decision.step}: {findings}")
        self.decision = decision


class ProgrammingWorkflow:
    def __init__(
        self,
        transport: AgentTransport | None = None,
        reviewer: RuleBasedReviewer | None = None,
        steps: Iterable[WorkflowStep] = DEFAULT_STEPS,
    ) -> None:
        self.transport = transport or DeterministicLocalTransport()
        self.reviewer = reviewer or RuleBasedReviewer()
        self.steps = tuple(steps)

    def run(self, requirement: str) -> WorkflowReport:
        results: list[AgentResult] = []
        reviews: list[ReviewDecision] = []
        context = requirement

        for step in self.steps:
            result = self.transport.run_agent(step, self._build_prompt(step, context, results))
            decision = self.reviewer.review(result)
            results.append(result)
            reviews.append(decision)

            if not decision.approved:
                raise ReviewGateError(decision)

            context = result.content

        return WorkflowReport(requirement=requirement, results=tuple(results), reviews=tuple(reviews))

    def _build_prompt(self, step: WorkflowStep, context: str, previous_results: list[AgentResult]) -> str:
        prior = "\n\n".join(result.content for result in previous_results)
        if not prior:
            return f"Step: {step}\nRequirement:\n{context}"
        return f"Step: {step}\nCurrent context:\n{context}\n\nApproved prior artifacts:\n{prior}"
