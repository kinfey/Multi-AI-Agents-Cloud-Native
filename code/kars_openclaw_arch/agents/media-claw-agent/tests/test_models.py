from datetime import UTC, date, datetime

import pytest
from media_claw_agent.models import AssetPaths, DailyManifest, Source, Story
from pydantic import ValidationError


def make_story(position: int) -> Story:
    return Story(
        position=position,
        market="CN" if position <= 5 else "US",
        title=f"市场动态第 {position} 条",
        title_en=f"Market update number {position}",
        summary="这是一条经过多来源核验的中美市场动态摘要。",
        summary_en="A verified summary of a China and U.S. market development.",
        narration=["第一句说明事件。", "第二句解释背景。", "第三句分析影响。", "第四句提示风险。"],
        narration_en=[
            "The first line states what happened.",
            "The second line gives the background.",
            "The third line explains the impact.",
            "The fourth line flags the risk.",
        ],
        key_points=["事件已确认", "关注后续影响"],
        key_points_en=["Event confirmed", "Watch the follow-through"],
        image_prompt="Professional editorial finance visual in a consistent 9:16 composition",
        sources=[Source(title="Example", url="https://example.com/story")],
    )


def test_asset_paths_separate_languages() -> None:
    paths = AssetPaths.for_date(date(2026, 7, 25))

    assert paths.cn.cover_image == "260725/imgs/cn/cover.png"
    assert paths.en.story_images == [f"260725/imgs/en/{index:02d}.png" for index in range(1, 11)]
    assert paths.cn.end_audio == "260725/audio/cn/end.wav"
    assert paths.en.cover_audio == "260725/audio/en/cover.wav"
    assert paths.cn.video == "260725/video/final_cn.mp4"
    assert paths.en.video == "260725/video/final_en.mp4"
    assert len(paths.cn.images) == len(paths.en.audio) == 12


def test_manifest_requires_exactly_ten_ordered_stories() -> None:
    manifest = DailyManifest(
        run_date=date(2026, 7, 25),
        generated_at=datetime.now(UTC),
        title="每日中美股市十条",
        title_en="Market Ten",
        stories=[make_story(index) for index in range(1, 11)],
        assets=AssetPaths.for_date(date(2026, 7, 25)),
    )

    assert len(manifest.stories) == 10
    assert manifest.title_for("en") == "Market Ten"

    with pytest.raises(ValidationError, match="exactly 10 stories"):
        DailyManifest(
            run_date=date(2026, 7, 25),
            generated_at=datetime.now(UTC),
            title="不完整日报",
            title_en="Incomplete briefing",
            stories=[make_story(1)],
            assets=AssetPaths.for_date(date(2026, 7, 25)),
        )
