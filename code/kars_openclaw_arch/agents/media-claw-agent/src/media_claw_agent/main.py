import argparse
from datetime import date

import structlog

from .config import get_settings
from .overlay import FrameRenderer
from .pipeline import MediaPipeline
from .providers import (
    FoundryEditorialProvider,
    ImageProvider,
    NewsProvider,
    build_speech_provider,
)
from .storage import MediaStorage
from .video import VideoComposer


def build_pipeline() -> MediaPipeline:
    logger = structlog.get_logger(__name__)
    logger.info("pipeline_build_started")
    settings = get_settings()
    credential = settings.credential()
    logger.info("storage_token_acquisition_started")
    credential.get_token("https://storage.azure.com/.default")
    logger.info("storage_token_acquisition_completed")
    pipeline = MediaPipeline(
        news=NewsProvider(settings.feeds),
        editor=FoundryEditorialProvider(settings),
        images=ImageProvider(settings),
        speech=build_speech_provider(settings),
        storage=MediaStorage(
            str(settings.azure_storage_account_url),
            settings.azure_storage_container,
            credential,
        ),
        composer=VideoComposer(),
        renderer=FrameRenderer(settings.media_font_cn, settings.media_font_en),
    )
    logger.info("pipeline_build_completed")
    return pipeline


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument(
        "--refresh-audio",
        action="store_true",
        help="reuse the existing manifest and images, replacing only audio and videos",
    )
    args = parser.parse_args()
    if args.refresh_audio and args.date is None:
        parser.error("--refresh-audio requires --date")
    pipeline = build_pipeline()
    structlog.get_logger(__name__).info(
        "daily_video_run_started", date=str(args.date) if args.date else None
    )
    manifest = (
        pipeline.refresh_audio_and_video(args.date)
        if args.refresh_audio
        else pipeline.run_if_needed(args.date)
    )
    structlog.get_logger().info(
        "daily_video_complete",
        date=str(manifest.run_date),
        video_cn=manifest.assets.cn.video,
        video_en=manifest.assets.en.video,
        durations=manifest.duration_seconds,
    )


if __name__ == "__main__":
    main()
