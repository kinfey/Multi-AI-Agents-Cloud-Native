from dataclasses import dataclass, field
from enum import StrEnum


class WorkflowStep(StrEnum):
    REQUIREMENTS = "requirements-agent"
    CODING = "coding-agent"
    TESTING = "testing-agent"
    DEPLOYMENT = "deployment-agent"
    SAVE = "save-agent"


@dataclass(frozen=True)
class AgentResult:
    step: WorkflowStep
    prompt: str
    content: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewDecision:
    step: WorkflowStep
    approved: bool
    score: int
    findings: tuple[str, ...]


@dataclass(frozen=True)
class WorkflowReport:
    requirement: str
    results: tuple[AgentResult, ...]
    reviews: tuple[ReviewDecision, ...]

    @property
    def approved(self) -> bool:
        return all(review.approved for review in self.reviews)
