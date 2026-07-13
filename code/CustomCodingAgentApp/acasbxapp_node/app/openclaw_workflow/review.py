from .models import AgentResult, ReviewDecision, WorkflowStep


REQUIRED_SECTIONS: dict[WorkflowStep, tuple[str, ...]] = {
    WorkflowStep.REQUIREMENTS: ("Acceptance Criteria", "Review Notes"),
    WorkflowStep.CODING: ("Implementation", "Files Changed", "Review Notes"),
    WorkflowStep.TESTING: ("Test Plan", "Test Evidence", "Review Notes"),
    WorkflowStep.DEPLOYMENT: (
        "Deployment Plan",
        "Azure Context",
        "Deployment Result",
        "Application URL",
        "Rollback",
        "Review Notes",
    ),
    WorkflowStep.SAVE: ("Archive", "Download URL", "Review Notes"),
}


class RuleBasedReviewer:
    def __init__(self, minimum_score: int = 80) -> None:
        self.minimum_score = minimum_score

    def review(self, result: AgentResult) -> ReviewDecision:
        required_sections = REQUIRED_SECTIONS[result.step]
        missing = [section for section in required_sections if section not in result.content]
        score = max(0, 100 - (len(missing) * 25))
        findings = tuple(f"Missing section: {section}" for section in missing)

        if result.step == WorkflowStep.DEPLOYMENT:
            if '"status":"succeeded"' not in result.content:
                findings = findings + ("Deployment did not report a succeeded status.",)
            if "https://" not in result.content:
                findings = findings + ("Deployment did not report an HTTPS application URL.",)

        if "[BLOCKED]" in result.content:
            score = min(score, 50)
            findings = findings + ("Agent marked the result as blocked.",)

        return ReviewDecision(
            step=result.step,
            approved=score >= self.minimum_score and not findings,
            score=score,
            findings=findings or ("Review passed.",),
        )
