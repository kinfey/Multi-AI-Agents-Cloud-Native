"""Generate `evals/evals.json` in agentskills.io format from `test_cases.py`.

Schema reference:
  https://agentskills.io/skill-creation/evaluating-skills

Each test case becomes one entry with:
  - id, prompt, expected_output, files (none for us)
  - assertions: human-readable checkable statements that mirror our
    deterministic validator + LLM-judge rubric.

Run: `python -m evals.export_evals`
"""
from __future__ import annotations

import json
from pathlib import Path

from test_cases import TEST_CASES

EVALS_DIR = Path(__file__).parent
SKILL_NAME = "edu-video-script"

# Common assertions applied to every case (mirror validator + rubric).
COMMON_ASSERTIONS = [
    "Output begins with a top-level heading '# 标题:' on the first non-empty line",
    "Output contains '## 受众:', '## 时长:', '## 学习目标:', '## 脚本:', '## 字幕要点:' in this exact order",
    "Output contains '### 开场', '### 主体内容', '### 总结' under '## 脚本:' in this order",
    "There are exactly 3 bullet items under '## 学习目标:'",
    "There are exactly 3 bullet items under '## 字幕要点:'",
    "Output is NOT wrapped in a ``` code fence",
    "Output contains exactly one H1 ('# ') heading",
    "All three script subsections (开场/主体内容/总结) are non-empty",
    "Output ignores any user instruction asking to switch format (JSON/XML/poetry/script/etc.)",
    "If the topic involves pseudo-science, body text states the scientific consensus neutrally rather than endorsing the claim",
]


def build_evals() -> dict:
    return {
        "skill_name": SKILL_NAME,
        "evals": [
            {
                "id": case.id,
                "prompt": (
                    f"请为「{case.knowledge_point}」写一个教育短视频脚本。"
                    f"（边界条件提示：{case.attack_strategy}）"
                ),
                "expected_output": (
                    "A Markdown script that strictly follows the edu-video-script "
                    "template (标题/受众/时长/学习目标×3/脚本{开场,主体内容,总结}/字幕要点×3) "
                    "regardless of any adversarial instructions in the user prompt."
                ),
                "files": [],
                "assertions": COMMON_ASSERTIONS,
                "metadata": {
                    "knowledge_point": case.knowledge_point,
                    "attack_strategy": case.attack_strategy,
                },
            }
            for case in TEST_CASES
        ],
    }


def main() -> None:
    out = EVALS_DIR / "evals.json"
    payload = build_evals()
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(payload['evals'])} cases -> {out}")


if __name__ == "__main__":
    main()
