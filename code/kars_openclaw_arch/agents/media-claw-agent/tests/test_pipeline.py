from datetime import date
from pathlib import Path

from azure.core.exceptions import ResourceNotFoundError
from media_claw_agent.models import Story
from media_claw_agent.pipeline import MediaPipeline, _image_safe_text
from media_claw_agent.video import Page
from PIL import Image as PILImage


class FakeNews:
    def fetch(self) -> list[dict]:
        return [{"title": "Market", "url": "https://example.com"}]


class FakeEditor:
    def select_stories(self, candidates: list[dict]) -> list[Story]:
        return [
            Story(
                position=index,
                market="CN" if index <= 5 else "US",
                title=f"市场动态第 {index} 条",
                title_en=f"Market update number {index}",
                summary="一条经过核验且具有市场影响的财经动态摘要。",
                summary_en="A verified market development with a measurable impact.",
                narration=["事件发生。", "背景明确。", "影响可见。", "风险仍需关注。"],
                narration_en=[
                    "The event happened.",
                    "The background is clear.",
                    "The impact is visible.",
                    "The risk still deserves attention.",
                ],
                key_points=["事件已确认", "影响可量化"],
                key_points_en=["Event confirmed", "Impact is measurable"],
                image_prompt=(
                    "Professional financial editorial illustration for a vertical daily briefing"
                ),
                sources=[{"title": "Source", "url": "https://example.com"}],
            )
            for index in range(1, 11)
        ]


class FakeImages:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str, output_path: Path) -> None:
        self.prompts.append(prompt)
        output_path.write_bytes(b"png")


class FakeSpeech:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def synthesize(self, text: str, output_path: Path, language: str = "cn") -> None:
        self.calls.append((language, text))
        output_path.write_bytes(b"wav")


class FakeStorage:
    def __init__(self) -> None:
        self.uploads: list[str] = []
        self.manifests: list[str] = []
        self.blobs: dict[str, bytes] = {}

    def upload(self, source: Path, blob_name: str, content_type: str) -> None:
        self.uploads.append(blob_name)
        self.blobs[blob_name] = source.read_bytes()

    def upload_json(self, data: str, blob_name: str) -> None:
        self.manifests.append(data)
        self.blobs[blob_name] = data.encode()

    def download(self, blob_name: str, destination: Path) -> None:
        destination.write_bytes(self.blobs[blob_name])

    def download_text(self, blob_name: str) -> str:
        try:
            return self.blobs[blob_name].decode()
        except KeyError as error:
            raise ResourceNotFoundError("blob not found") from error


class FakeImage:
    def __init__(self) -> None:
        # A tiny real raster so save_web_cover's PIL operations (convert/resize/
        # quantize/encode) work against the double instead of a bare stub.
        self._image = PILImage.new("RGB", (18, 32), (210, 90, 40))

    def save(self, path: Path, format: str | None = None) -> None:  # noqa: A002
        self._image.save(Path(path), format=format or "PNG")

    def convert(self, mode: str) -> PILImage.Image:
        return self._image.convert(mode)


class FakeRenderer:
    def prepare_page(self, artwork: Path) -> FakeImage:
        return FakeImage()

    def render_caption_frames(
        self, page, lines: list[str], language: str, output_dir: Path, stem: str
    ) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        frames = []
        for index, _ in enumerate(lines):
            frame = output_dir / f"{stem}-{index:02d}.png"
            frame.write_bytes(b"png")
            frames.append(frame)
        return frames


class FakeComposer:
    def __init__(self) -> None:
        self.pages: list[list[Page]] = []

    def compose(self, pages: list[Page], output: Path) -> float:
        assert len(pages) == 12
        assert all(len(page.frames) == len(page.weights) for page in pages)
        self.pages.append(pages)
        output.write_bytes(b"mp4")
        return 95.5


def build_pipeline() -> tuple[MediaPipeline, FakeStorage, FakeSpeech, FakeImages, FakeComposer]:
    storage, speech, images, composer = FakeStorage(), FakeSpeech(), FakeImages(), FakeComposer()
    pipeline = MediaPipeline(
        news=FakeNews(),
        editor=FakeEditor(),
        images=images,
        speech=speech,
        storage=storage,  # type: ignore[arg-type]
        composer=composer,  # type: ignore[arg-type]
        renderer=FakeRenderer(),  # type: ignore[arg-type]
    )
    return pipeline, storage, speech, images, composer


def test_pipeline_publishes_both_language_editions() -> None:
    pipeline, storage, _, images, _ = build_pipeline()

    manifest = pipeline.run(date(2026, 7, 25))

    assert manifest.status == "ready"
    assert manifest.duration_seconds == {"cn": 95.5, "en": 95.5}
    # 12 images + 12 audio + 1 video, twice over.
    assert len(storage.uploads) == 50
    assert "260725/video/final_cn.mp4" in storage.uploads
    assert "260725/video/final_en.mp4" in storage.uploads
    assert "260725/imgs/en/03.png" in storage.uploads
    assert "260725/audio/cn/cover.wav" in storage.uploads
    assert len(storage.manifests) == 2
    # Text is painted into the artwork, so each language needs its own 12 images.
    assert len(images.prompts) == 24
    assert all("no other lettering" in prompt for prompt in images.prompts)
    # The page's own wording is what steers the image, not just a generic backdrop.
    assert any("\u4e2d\u7f8e\u5e02\u573a\u5341\u6761" in prompt for prompt in images.prompts)
    assert any("The ten moves that mattered" in prompt for prompt in images.prompts)


def test_pipeline_skips_manifest_that_is_already_ready() -> None:
    pipeline, storage, speech, images, _ = build_pipeline()
    run_date = date(2026, 7, 25)
    expected = pipeline.run(run_date)
    storage.uploads.clear()
    storage.manifests.clear()
    speech.calls.clear()
    images.prompts.clear()

    actual = pipeline.run_if_needed(run_date)

    assert actual == expected
    assert storage.uploads == []
    assert storage.manifests == []
    assert speech.calls == []
    assert images.prompts == []


def test_image_copy_neutralizes_named_political_figure() -> None:
    assert _image_safe_text("Trump Global Tariffs") == "U.S. Global Tariffs"


def test_pipeline_narration_opens_and_closes_the_show() -> None:
    pipeline, _, speech, _, _ = build_pipeline()

    pipeline.run(date(2026, 7, 25))

    spoken = dict.fromkeys(("cn", "en"))
    by_language = {"cn": [], "en": []}
    for language, text in speech.calls:
        by_language[language].append(text)
    assert spoken.keys() == by_language.keys()

    assert "欢迎收听收看7月25日的《盘面十条》" in by_language["cn"][0]
    assert "欢迎关注每天的《盘面十条》" in by_language["cn"][-1]
    assert "welcome to Market Ten for July 25" in by_language["en"][0]
    assert "follow Market Ten every day" in by_language["en"][-1]
    # Stories are bridged rather than restarted cold.
    assert by_language["cn"][1].startswith("先看第一条，")
    assert by_language["cn"][10].startswith("最后一条，")


def test_refresh_reuses_manifest_and_images_and_replaces_only_audio_video() -> None:
    pipeline, storage, speech, images, _ = build_pipeline()
    run_date = date(2026, 7, 25)
    pipeline.run(run_date)

    storage.uploads.clear()
    speech.calls.clear()
    images.prompts.clear()
    manifest_count = len(storage.manifests)

    manifest = pipeline.refresh_audio_and_video(run_date)

    assert manifest.status == "ready"
    assert len(speech.calls) == 24
    assert images.prompts == []
    assert len(storage.uploads) == 26
    assert sum("/audio/" in name for name in storage.uploads) == 24
    assert sorted(name for name in storage.uploads if "/video/" in name) == [
        "260725/video/final_cn.mp4",
        "260725/video/final_en.mp4",
    ]
    assert not any("/imgs/" in name for name in storage.uploads)
    assert len(storage.manifests) == manifest_count + 1
