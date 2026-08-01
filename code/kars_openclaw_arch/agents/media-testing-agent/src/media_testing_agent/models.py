from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Finding(BaseModel):
    id: str = Field(pattern=r"^RT-\d{4}-\d{2,}$")
    title: str
    severity: Literal["critical", "high", "medium", "low"]
    corpus: str
    status: Literal["open", "accepted", "closed"]
    summary: str


class EvalReport(BaseModel):
    schema_version: int = 1
    run_id: str
    created_at: datetime
    target: str = "media-claw-agent"
    total_cases: int = Field(ge=0)
    passed_cases: int = Field(ge=0)
    failed_cases: int = Field(ge=0)
    findings: list[Finding] = []

    @property
    def passed(self) -> bool:
        return self.failed_cases == 0 and not any(
            finding.severity in {"critical", "high"} and finding.status == "open"
            for finding in self.findings
        )

    @property
    def pass_rate(self) -> float:
        # Security score driven only by unresolved high/critical findings.
        # Medium/low findings and any accepted/closed finding do not lower the
        # score, so reclassified or accepted issues stop dragging the percentage
        # down. Each open critical/high finding removes one case worth of credit.
        if not self.total_cases:
            return 0.0
        blocking = sum(
            1
            for finding in self.findings
            if finding.severity in {"critical", "high"} and finding.status == "open"
        )
        blocking = min(blocking, self.total_cases)
        return (self.total_cases - blocking) / self.total_cases

    def public_dict(self) -> dict:
        return {
            **self.model_dump(mode="json"),
            "passed": self.passed,
            "pass_rate": self.pass_rate,
        }
