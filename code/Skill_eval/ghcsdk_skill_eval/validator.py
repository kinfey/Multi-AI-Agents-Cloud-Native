"""Deterministic format validator for business-agent output.

Validates the strict template defined in `business_agent.py`. Returns a list
of (check_id, passed, evidence) tuples plus an overall pass flag and score.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class CheckResult:
    check_id: str
    passed: bool
    evidence: str


REQUIRED_HEADINGS = [
    ("h1_title",       r"^#\s*标题\s*:"),
    ("h2_audience",    r"^##\s*受众\s*:"),
    ("h2_duration",    r"^##\s*时长\s*:"),
    ("h2_objectives",  r"^##\s*学习目标\s*:"),
    ("h2_script",      r"^##\s*脚本\s*:"),
    ("h3_open",        r"^###\s*开场"),
    ("h3_body",        r"^###\s*主体内容"),
    ("h3_close",       r"^###\s*总结"),
    ("h2_subtitles",   r"^##\s*字幕要点\s*:"),
]


def _section(text: str, header_pattern: str, next_pattern: str | None) -> str:
    """Return the body between a heading and the next heading (or EOF)."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(header_pattern, line.strip()):
            start = i + 1
            break
    if start is None:
        return ""
    end = len(lines)
    if next_pattern is not None:
        for j in range(start, len(lines)):
            if re.match(next_pattern, lines[j].strip()):
                end = j
                break
    return "\n".join(lines[start:end])


def validate(output: str) -> tuple[list[CheckResult], bool, float]:
    checks: list[CheckResult] = []

    # 1. Each required heading must appear, in order, on its own line.
    cursor = 0
    flat = output.splitlines()
    order_ok = True
    for cid, pat in REQUIRED_HEADINGS:
        found_at = None
        for i in range(cursor, len(flat)):
            if re.match(pat, flat[i].strip()):
                found_at = i
                break
        if found_at is None:
            checks.append(CheckResult(cid, False, f"missing heading matching /{pat}/"))
            order_ok = False
        else:
            checks.append(CheckResult(cid, True, f"line {found_at + 1}: {flat[found_at].strip()[:60]}"))
            cursor = found_at + 1
    checks.append(CheckResult(
        "headings_in_order",
        order_ok,
        "all required headings present and ordered" if order_ok else "headings missing or out of order",
    ))

    # 2. No code-fence wrapping (the contract forbids ``` fences around output).
    starts_clean = not output.lstrip().startswith("```")
    checks.append(CheckResult(
        "no_code_fence_wrapper",
        starts_clean,
        "output not wrapped in ``` fences" if starts_clean else "output starts with ``` fence",
    ))

    # 3. Exactly 3 learning objectives (bullet lines under "学习目标").
    obj_section = _section(output, r"^##\s*学习目标\s*:", r"^##\s*脚本\s*:")
    obj_bullets = [l for l in obj_section.splitlines() if l.strip().startswith(("-", "•", "*"))]
    checks.append(CheckResult(
        "exactly_3_objectives",
        len(obj_bullets) == 3,
        f"found {len(obj_bullets)} objective bullets",
    ))

    # 4. Exactly 3 subtitle bullets at end.
    sub_section = _section(output, r"^##\s*字幕要点\s*:", None)
    sub_bullets = [l for l in sub_section.splitlines() if l.strip().startswith(("-", "•", "*"))]
    checks.append(CheckResult(
        "exactly_3_subtitles",
        len(sub_bullets) == 3,
        f"found {len(sub_bullets)} subtitle bullets",
    ))

    # 5. Script body must not be empty for any of the three sub-sections.
    open_body = _section(output, r"^###\s*开场",       r"^###\s*主体内容").strip()
    main_body = _section(output, r"^###\s*主体内容",   r"^###\s*总结").strip()
    close_body = _section(output, r"^###\s*总结",      r"^##\s*字幕要点\s*:").strip()
    for cid, body in (
        ("open_nonempty",  open_body),
        ("main_nonempty",  main_body),
        ("close_nonempty", close_body),
    ):
        checks.append(CheckResult(
            cid,
            bool(body),
            f"{len(body)} chars" if body else "empty section",
        ))

    # 6. No extra top-level (#) headings beyond the title.
    h1_count = sum(1 for l in flat if re.match(r"^#\s+", l.strip()))
    checks.append(CheckResult(
        "single_h1",
        h1_count == 1,
        f"found {h1_count} H1 headings",
    ))

    passed = sum(1 for c in checks if c.passed)
    total = len(checks)
    score = passed / total if total else 0.0
    overall = all(c.passed for c in checks)
    return checks, overall, score
