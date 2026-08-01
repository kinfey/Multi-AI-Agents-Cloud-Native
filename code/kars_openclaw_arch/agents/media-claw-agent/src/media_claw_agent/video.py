from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

FRAME_WIDTH = 1080
FRAME_HEIGHT = 1920


@dataclass(frozen=True)
class Page:
    """One narrated page: caption frames sharing a background, plus its audio.

    `frames` are pre-rendered stills that differ only in the burned caption, so
    swapping frames advances the subtitle without needing any text filter in
    ffmpeg (this build ships without libass/freetype).
    """

    frames: list[Path]
    weights: list[float]
    audio: Path


class VideoComposer:
    # Long enough to read as a deliberate transition rather than a decode
    # glitch. The tail of silence appended to every page is longer than this so
    # the crossfade always overlaps silence instead of clipping narration.
    TRANSITION_SECONDS = 0.7
    TAIL_SECONDS = 1.0
    FPS = 30
    # Cycled so two consecutive page changes never look identical.
    TRANSITIONS = ("fade", "slideleft", "wipeleft", "circleopen", "slideup", "smoothright")

    def __init__(self, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> None:
        self._ffmpeg = ffmpeg
        self._ffprobe = ffprobe

    def compose(self, pages: list[Page], output: Path) -> float:
        if not pages:
            raise ValueError("at least one page is required")
        segment_dir = output.parent / f"{output.stem}-segments"
        segment_dir.mkdir(parents=True, exist_ok=True)
        segments = [
            self._encode_page(page, segment_dir / f"{index:02d}.mp4")
            for index, page in enumerate(pages)
        ]
        if len(segments) == 1:
            segments[0].replace(output)
        else:
            self._crossfade(segments, output)
        return self.duration(output)

    def _encode_page(self, page: Page, segment: Path) -> Path:
        if len(page.frames) != len(page.weights):
            raise ValueError("every caption frame needs a duration weight")
        speech = self.duration(page.audio)
        total = speech + self.TAIL_SECONDS
        total_weight = sum(page.weights) or float(len(page.weights))
        durations = [speech * weight / total_weight for weight in page.weights]
        durations[-1] += self.TAIL_SECONDS

        concat_file = segment.with_suffix(".txt")
        entries = "".join(
            f"file '{frame.resolve()}'\nduration {duration:.3f}\n"
            for frame, duration in zip(page.frames, durations, strict=True)
        )
        # The concat demuxer ignores the duration of the final entry, so the last
        # frame is repeated to make its declared duration take effect.
        concat_file.write_text(f"{entries}file '{page.frames[-1].resolve()}'\n", encoding="utf-8")
        # fmt: off
        self._run(
            [
                self._ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
                "-i", str(page.audio),
                "-filter_complex",
                (
                    f"[0:v]fps={self.FPS},"
                    f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}:force_original_aspect_ratio=increase,"
                    f"crop={FRAME_WIDTH}:{FRAME_HEIGHT},format=yuv420p,setsar=1[v];"
                    f"[1:a]aresample=48000,apad=whole_dur={total:.3f}[a]"
                ),
                "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
                "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(segment),
            ]
        )
        # fmt: on
        return segment

    def _crossfade(self, segments: list[Path], output: Path) -> None:
        durations = [self.duration(segment) for segment in segments]
        transition = self.TRANSITION_SECONDS
        inputs: list[str] = []
        for segment in segments:
            inputs += ["-i", str(segment)]

        filters: list[str] = []
        video_label, audio_label = "0:v", "0:a"
        # xfade overlaps its two inputs, so every join shortens the timeline by
        # `transition`; the running total must account for that or each offset
        # after the first drifts late and the previous page freezes.
        elapsed = durations[0]
        for index in range(1, len(segments)):
            offset = elapsed - transition
            effect = self.TRANSITIONS[(index - 1) % len(self.TRANSITIONS)]
            next_video, next_audio = f"vx{index}", f"ax{index}"
            filters.append(
                f"[{video_label}][{index}:v]xfade=transition={effect}:"
                f"duration={transition}:offset={offset:.3f}[{next_video}]"
            )
            filters.append(
                f"[{audio_label}][{index}:a]acrossfade=d={transition}:c1=tri:c2=tri[{next_audio}]"
            )
            video_label, audio_label = next_video, next_audio
            elapsed += durations[index] - transition

        # fmt: off
        self._run(
            [
                self._ffmpeg, "-y", *inputs,
                "-filter_complex", ";".join(filters),
                "-map", f"[{video_label}]", "-map", f"[{audio_label}]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output),
            ]
        )
        # fmt: on

    def duration(self, media: Path) -> float:
        result = self._run(
            [self._ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", str(media)]
        )
        return float(json.loads(result.stdout)["format"]["duration"])

    @staticmethod
    def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, check=True, capture_output=True, text=True)
