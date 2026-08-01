from datetime import UTC, datetime

from media_testing_agent.models import EvalReport, Finding


def test_report_fails_with_open_high_finding() -> None:
    report = EvalReport(
        run_id="eval-260725",
        created_at=datetime.now(UTC),
        total_cases=20,
        passed_cases=19,
        failed_cases=1,
        findings=[
            Finding(
                id="RT-2026-05",
                title="Prompt injection crossed the tool boundary",
                severity="high",
                corpus="prompt-injection-2026q1",
                status="open",
                summary="The sandbox attempted a denied outbound action.",
            )
        ],
    )

    assert report.passed is False
    assert report.pass_rate == 0.95
