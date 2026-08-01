"""Builds the spoken script with explicit continuity between pages.

The opening and closing lines are generated here rather than by the editorial
model so the show's framing is guaranteed on every run: the cover always
welcomes the audience to that specific date's 《盘面十条》 / Market Ten and the end
card always asks them to follow the show daily. The model only supplies the ten
story bodies; this module wraps them with connectives so the narration flows from
one page into the next instead of restarting cold on each item.

The two show names are the single source of truth for branding. Anything that
speaks or prints the title -- narration, page headings, key points, the manifest
title, the web masthead -- derives from them, so renaming the show is a one-line
change here rather than a hunt through the codebase.
"""

from __future__ import annotations

from datetime import date

from .models import DailyManifest, Language, Story

SHOW_NAME_CN = "《盘面十条》"
SHOW_NAME_EN = "Market Ten"

# fmt: off
_EN_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
# fmt: on


def spoken_date(run_date: date, language: Language) -> str:
    if language == "cn":
        return f"{run_date.month}月{run_date.day}日"
    return f"{_EN_MONTHS[run_date.month - 1]} {run_date.day}"


def opening_lines(run_date: date, language: Language) -> list[str]:
    stamp = spoken_date(run_date, language)
    if language == "cn":
        return [
            f"大家好，欢迎收听收看{stamp}的{SHOW_NAME_CN}。",
            "接下来的十条，我们把中美两地市场当天最值得关注的变化一次说清。",
            "话不多说，我们从第一条开始。",
        ]
    return [
        f"Hello, and welcome to {SHOW_NAME_EN} for {stamp}.",
        "In the next few minutes we walk through the ten developments that matter most "
        "across the Chinese and U.S. markets today.",
        "Let's get straight into the first one.",
    ]


def closing_lines(run_date: date, language: Language) -> list[str]:
    stamp = spoken_date(run_date, language)
    if language == "cn":
        return [
            f"以上就是{stamp}的{SHOW_NAME_CN}全部内容。",
            "以上信息仅供参考，不构成任何投资建议，请理性判断、风险自担。",
            f"欢迎关注每天的{SHOW_NAME_CN}，我们明天同一时间再见。",
        ]
    return [
        f"That is the full rundown of {SHOW_NAME_EN} for {stamp}.",
        "Everything here is for information only and is not investment advice, "
        "so please make your own judgement and manage your own risk.",
        f"Please follow {SHOW_NAME_EN} every day, and we will see you at the same time tomorrow.",
    ]


def _connector(position: int, language: Language) -> str:
    if language == "cn":
        if position == 1:
            return "先看第一条，"
        if position == 10:
            return "最后一条，"
        return f"接下来第{position}条，"
    if position == 1:
        return "First up, "
    if position == 10:
        return "And finally, "
    return f"Next, story {position}. "


def story_lines(story: Story, language: Language) -> list[str]:
    """Narration for one story, with a connective phrase leading into it."""
    lines = list(story.narration_for(language))
    lines[0] = f"{_connector(story.position, language)}{lines[0]}"
    return lines


def page_scripts(manifest: DailyManifest, language: Language) -> list[list[str]]:
    """Twelve pages of script: cover, ten stories, end card."""
    return [
        opening_lines(manifest.run_date, language),
        *[story_lines(story, language) for story in manifest.stories],
        closing_lines(manifest.run_date, language),
    ]


def page_headings(manifest: DailyManifest, language: Language) -> list[str]:
    cover = manifest.title_for(language)
    end = f"关注每天的{SHOW_NAME_CN}" if language == "cn" else f"Follow {SHOW_NAME_EN} every day"
    return [cover, *[story.title_for(language) for story in manifest.stories], end]


def page_key_points(manifest: DailyManifest, language: Language) -> list[list[str]]:
    stamp = spoken_date(manifest.run_date, language)
    if language == "cn":
        cover = [f"{stamp} 中美市场十条", "一次看懂当天关键变化"]
        end = ["理性判断 风险自担", f"每日更新 {SHOW_NAME_CN}"]
    else:
        # The cover heading already carries "Market Ten · <date>", so these two
        # lines add the pitch rather than repeating the brand.
        cover = ["The ten moves that mattered", "China and the U.S. in one briefing"]
        end = ["Information only, not investment advice", f"New {SHOW_NAME_EN} every weekday"]
    return [cover, *[story.key_points_for(language) for story in manifest.stories], end]
