from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

import structlog
from azure.core.exceptions import ResourceNotFoundError

from . import script
from .models import LANGUAGES, AssetPaths, DailyManifest, Language
from .overlay import FrameRenderer, save_web_cover
from .storage import MediaStorage
from .video import Page, VideoComposer

logger = structlog.get_logger(__name__)


def _image_safe_text(text: str) -> str:
    return text.replace("Trump", "U.S.")


def _text_rule(heading: str, key_points: list[str], language: Language) -> str:
    """Fold one page's wording into its image prompt.

    The headline and key points are painted by the image model itself rather than
    composited afterwards, so the artwork is generated *around* the message. The
    cost is that a page can no longer be shared between languages: the Chinese and
    English editions each need their own image.

    The bottom quarter is reserved because spoken-line subtitles are still burned
    in by Pillow on top of whatever the model returns.
    """
    if language == "cn":
        script_hint = "The text is Simplified Chinese; reproduce every character exactly"
        quoted = " / ".join(f"\u201c{point}\u201d" for point in key_points)
    else:
        script_hint = "The text is English; reproduce every word with correct spelling"
        quoted = " / ".join(f'"{point}"' for point in key_points)
    return (
        " Render this exact text into the artwork as clean, sharp editorial typography. "
        f"{script_hint}, and add no words beyond the ones given. "
        f'Headline across the upper third: "{heading}". '
        f"Directly below it, {len(key_points)} short bullet lines: {quoted}. "
        "Set the text on a dark translucent panel so it stays legible against the art, "
        "keep the bottom quarter of the frame completely free of text, and include no "
        "logos, no watermarks and no other lettering."
    )


class MediaPipeline:
    def __init__(
        self,
        news,  # noqa: ANN001 - duck-typed providers, see main.build_pipeline
        editor,  # noqa: ANN001
        images,  # noqa: ANN001
        speech,  # noqa: ANN001
        storage: MediaStorage,
        composer: VideoComposer,
        renderer: FrameRenderer | None = None,
    ) -> None:
        self._news = news
        self._editor = editor
        self._images = images
        self._speech = speech
        self._storage = storage
        self._composer = composer
        self._renderer = renderer or FrameRenderer()

    def run_if_needed(self, run_date: date | None = None) -> DailyManifest:
        selected_date = run_date or datetime.now(UTC).date()
        manifest_path = f"{selected_date:%y%m%d}/manifest.json"
        logger.info("daily_manifest_check_started", date=str(selected_date))
        try:
            manifest = DailyManifest.model_validate_json(
                self._storage.download_text(manifest_path)
            )
        except ResourceNotFoundError:
            logger.info("daily_manifest_missing", date=str(selected_date))
            return self.run(selected_date)

        logger.info(
            "daily_manifest_loaded", date=str(selected_date), status=manifest.status
        )
        if manifest.status == "ready":
            logger.info("daily_video_already_ready", date=str(selected_date))
            return manifest
        return self.run(selected_date)

    def run(self, run_date: date | None = None) -> DailyManifest:
        selected_date = run_date or datetime.now(UTC).date()
        stories = self._editor.select_stories(self._news.fetch())
        manifest = DailyManifest(
            run_date=selected_date,
            generated_at=datetime.now(UTC),
            title=f"{selected_date:%Y年%m月%d日} 中美股市十条",
            title_en=f"{script.SHOW_NAME_EN} · {selected_date:%B %d, %Y}",
            stories=stories,
            assets=AssetPaths.for_date(selected_date),
        )
        prefix = selected_date.strftime("%y%m%d")
        self._storage.upload_json(manifest.model_dump_json(indent=2), f"{prefix}/manifest.json")
        try:
            with TemporaryDirectory(prefix="finance-video-") as temp:
                workdir = Path(temp)
                for language in LANGUAGES:
                    self._render_language(manifest, language, workdir)
            manifest.status = "ready"
        except Exception:
            manifest.status = "failed"
            self._storage.upload_json(manifest.model_dump_json(indent=2), f"{prefix}/manifest.json")
            raise
        self._storage.upload_json(manifest.model_dump_json(indent=2), f"{prefix}/manifest.json")
        return manifest

    def refresh_audio_and_video(self, run_date: date) -> DailyManifest:
        """Replace narration and composed videos while preserving editorial assets."""
        prefix = run_date.strftime("%y%m%d")
        logger.info("audio_refresh_started", date=str(run_date), prefix=prefix)
        manifest = DailyManifest.model_validate_json(
            self._storage.download_text(f"{prefix}/manifest.json")
        )
        logger.info(
            "audio_refresh_manifest_loaded",
            date=str(run_date),
            stories=len(manifest.stories),
            status=manifest.status,
        )
        if manifest.run_date != run_date:
            raise ValueError(
                f"manifest date {manifest.run_date} does not match requested date {run_date}"
            )

        with TemporaryDirectory(prefix="finance-video-refresh-") as temp:
            workdir = Path(temp)
            uploads: list[tuple[Path, str, str]] = []
            durations: dict[Language, float] = {}
            for language in LANGUAGES:
                logger.info("audio_refresh_language_started", language=language)
                language_uploads, duration = self._prepare_refreshed_language(
                    manifest, language, workdir
                )
                uploads.extend(language_uploads)
                durations[language] = duration
                logger.info(
                    "audio_refresh_language_prepared",
                    language=language,
                    duration_seconds=round(duration, 3),
                    uploads=len(language_uploads),
                )

            for upload_index, (source, blob_name, content_type) in enumerate(uploads, start=1):
                logger.info(
                    "audio_refresh_upload_started",
                    upload=upload_index,
                    total_uploads=len(uploads),
                    blob=blob_name,
                    bytes=source.stat().st_size,
                    content_type=content_type,
                )
                self._storage.upload(source, blob_name, content_type)
                logger.info(
                    "audio_refresh_upload_completed",
                    upload=upload_index,
                    total_uploads=len(uploads),
                    blob=blob_name,
                )

        manifest.duration_seconds = durations
        manifest.status = "ready"
        self._storage.upload_json(
            manifest.model_dump_json(indent=2), f"{prefix}/manifest.json"
        )
        logger.info(
            "audio_refresh_completed",
            date=str(run_date),
            uploads=len(uploads),
            durations=durations,
        )
        return manifest

    def _prepare_refreshed_language(
        self,
        manifest: DailyManifest,
        language: Language,
        workdir: Path,
    ) -> tuple[list[tuple[Path, str, str]], float]:
        assets = manifest.assets.for_language(language)
        language_dir = workdir / language
        language_dir.mkdir(parents=True, exist_ok=True)
        scripts = script.page_scripts(manifest, language)
        joiner = "" if language == "cn" else " "
        pages: list[Page] = []
        uploads: list[tuple[Path, str, str]] = []

        bundle = zip(assets.images, scripts, assets.audio, strict=True)
        for index, (image_blob, lines, audio_blob) in enumerate(bundle):
            page_number = index + 1
            logger.info(
                "audio_refresh_page_started",
                language=language,
                page=page_number,
                total_pages=len(assets.images),
                image_blob=image_blob,
                audio_blob=audio_blob,
            )
            source_image = language_dir / f"source-{index:02d}.png"
            step_started = perf_counter()
            self._storage.download(image_blob, source_image)
            logger.info(
                "audio_refresh_image_downloaded",
                language=language,
                page=page_number,
                bytes=source_image.stat().st_size,
                elapsed_seconds=round(perf_counter() - step_started, 3),
            )
            page_image = self._renderer.prepare_page(source_image)

            audio_path = language_dir / f"{index:02d}.wav"
            step_started = perf_counter()
            logger.info(
                "audio_refresh_synthesis_started",
                language=language,
                page=page_number,
                characters=len(joiner.join(lines)),
            )
            self._speech.synthesize(joiner.join(lines), audio_path, language)
            logger.info(
                "audio_refresh_synthesis_completed",
                language=language,
                page=page_number,
                bytes=audio_path.stat().st_size,
                elapsed_seconds=round(perf_counter() - step_started, 3),
            )
            uploads.append((audio_path, audio_blob, "audio/wav"))

            step_started = perf_counter()
            frames = self._renderer.render_caption_frames(
                page_image, lines, language, language_dir / "frames", f"{index:02d}"
            )
            logger.info(
                "audio_refresh_captions_rendered",
                language=language,
                page=page_number,
                frames=len(frames),
                elapsed_seconds=round(perf_counter() - step_started, 3),
            )
            pages.append(
                Page(
                    frames=frames,
                    weights=[float(max(len(line), 1)) for line in lines],
                    audio=audio_path,
                )
            )

        video_path = workdir / f"final_{language}.mp4"
        step_started = perf_counter()
        logger.info(
            "audio_refresh_video_started", language=language, pages=len(pages)
        )
        duration = self._composer.compose(pages, video_path)
        logger.info(
            "audio_refresh_video_completed",
            language=language,
            duration_seconds=round(duration, 3),
            bytes=video_path.stat().st_size,
            elapsed_seconds=round(perf_counter() - step_started, 3),
        )
        uploads.append((video_path, assets.video, "video/mp4"))
        return uploads, duration

    def _generate_pages(
        self, manifest: DailyManifest, language: Language, workdir: Path
    ) -> list[Path]:
        """One image per page for a single language, text already painted in."""
        page_dir = workdir / f"pages-{language}"
        page_dir.mkdir(parents=True, exist_ok=True)
        scenes = [
            self._cover_prompt(),
            *[story.image_prompt for story in manifest.stories],
            self._end_prompt(),
        ]
        headings = script.page_headings(manifest, language)
        key_points = script.page_key_points(manifest, language)
        images: list[Path] = []
        bundle = zip(scenes, headings, key_points, strict=True)
        for index, (scene, heading, points) in enumerate(bundle):
            path = page_dir / f"{index:02d}.png"
            try:
                self._images.generate(
                    f"{scene}{_text_rule(_image_safe_text(heading), points, language)}", path
                )
            except Exception as error:
                raise RuntimeError(
                    f"{language} image page {index:02d} failed: {heading}"
                ) from error
            images.append(path)
        return images

    def _render_language(
        self,
        manifest: DailyManifest,
        language: Language,
        workdir: Path,
    ) -> None:
        assets = manifest.assets.for_language(language)
        language_dir = workdir / language
        language_dir.mkdir(parents=True, exist_ok=True)
        page_images = self._generate_pages(manifest, language, workdir)
        scripts = script.page_scripts(manifest, language)
        joiner = "" if language == "cn" else " "

        pages: list[Page] = []
        bundle = zip(page_images, scripts, assets.images, assets.audio, strict=True)
        for index, (artwork, lines, image_blob, audio_blob) in enumerate(bundle):
            page_image = self._renderer.prepare_page(artwork)
            image_path = language_dir / f"{index:02d}.png"
            if index == 0:
                # The cover doubles as the web thumbnail/poster; upload a light
                # copy while keeping the full-resolution frame for the video.
                save_web_cover(page_image, image_path)
            else:
                page_image.save(image_path, format="PNG")
            self._storage.upload(image_path, image_blob, "image/png")

            audio_path = language_dir / f"{index:02d}.wav"
            self._speech.synthesize(joiner.join(lines), audio_path, language)
            self._storage.upload(audio_path, audio_blob, "audio/wav")

            frames = self._renderer.render_caption_frames(
                page_image, lines, language, language_dir / "frames", f"{index:02d}"
            )
            pages.append(
                Page(
                    frames=frames,
                    weights=[float(max(len(line), 1)) for line in lines],
                    audio=audio_path,
                )
            )

        video_path = workdir / f"final_{language}.mp4"
        manifest.duration_seconds[language] = self._composer.compose(pages, video_path)
        self._storage.upload(video_path, assets.video, "video/mp4")

    @staticmethod
    def _cover_prompt() -> str:
        return (
            "9:16 vertical professional financial editorial cover artwork, abstract Chinese and "
            "US market motifs, candlestick and index-curve textures, vermilion, ink black and "
            "mint accents, cinematic depth, premium broadcast look."
        )

    @staticmethod
    def _end_prompt() -> str:
        return (
            "9:16 vertical professional financial editorial end card artwork, calm closing "
            "composition, consistent vermilion, ink black and mint visual system, soft gradient "
            "and subtle paper grain, premium broadcast look."
        )
