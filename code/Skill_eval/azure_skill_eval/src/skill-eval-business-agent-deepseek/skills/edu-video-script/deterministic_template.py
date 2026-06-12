"""Deterministic skeleton generator for the `edu-video-script` skill.

Invoked by the agent through `run_skill_script("deterministic-template", ...)`.
Returns a Markdown skeleton guaranteed to pass `shared.validator.validate`.
"""
from __future__ import annotations

import json
import re
import sys


def _slugify(text: str) -> str:
    text = (text or "").strip() or "未命名知识点"
    return re.sub(r"\s+", " ", text)


def render(knowledge_point: str) -> str:
    kp = _slugify(knowledge_point)
    return (
        f"# 教学短视频:{kp}\n\n"
        "## 目标受众\n"
        "对该知识点零基础或入门阶段的学习者(青少年 / 成人通识皆可)。\n\n"
        "## 时长:3 分钟\n\n"
        "## 学习目标\n"
        f"- 能够用一句话向他人解释「{kp}」是什么。\n"
        f"- 能够举出至少一个生活/工程中的「{kp}」实例。\n"
        f"- 能够指出与「{kp}」相关的一个常见误解并加以澄清。\n\n"
        "## 脚本\n\n"
        "### 开场 (0:00 - 0:30)\n"
        f"今天我们用 3 分钟拆解一个看似简单但常被搞混的概念——「{kp}」。\n"
        "先抛一个反直觉的小问题作为钩子,把观众目光锁住。\n\n"
        "### 主体讲解 (0:30 - 2:30)\n"
        f"第一步,给出「{kp}」的清晰定义,并用一个比喻让观众产生具象的画面。"
        f"第二步,展开一个具体例子,演示「{kp}」是如何在真实场景中发生作用的。"
        "第三步,对比一个最常见的错误理解,指出它哪里走偏、为什么会走偏。\n\n"
        "### 收尾 (2:30 - 3:00)\n"
        f"回顾今天的三个学习目标,提示观众用一句话复述「{kp}」并尝试举一个自己的例子。\n"
        "鼓励他们在评论区留下自己的复述,以便我们在下一期视频里点评。\n\n"
        "## 字幕要点\n"
        f"- {kp}:一句话定义 + 直观比喻。\n"
        "- 真实场景的一个具体例子,展示其作用。\n"
        "- 常见误解 + 澄清,避免观众继续误用。\n"
    )


def main() -> None:
    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    kp = payload.get("knowledge_point") or payload.get("topic") or ""
    sys.stdout.write(render(kp))


if __name__ == "__main__":
    main()
