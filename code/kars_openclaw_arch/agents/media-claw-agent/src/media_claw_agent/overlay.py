"""Pillow text compositing for bilingual frames.

All burned-in text is rendered here rather than by ffmpeg. The ffmpeg binary
this project runs against (Homebrew 8.1.2) is built without libass, libfreetype
and fontconfig, so the `subtitles`, `ass` and `drawtext` filters are all absent
from `ffmpeg -filters`. Attempting to burn captions in the filter graph fails
with "No such filter", so text has to be baked into the PNG frames instead.

Rendering at the final 1080x1920 frame size (rather than the 768x1360 the image
model returns) keeps glyph edges crisp: ffmpeg then never has to upscale text.
"""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FRAME_WIDTH = 1080
FRAME_HEIGHT = 1920

# Cover images double as the web thumbnail and the <video> poster, so they are
# fetched on the catalog's first paint. A full 1080x1920 editorial PNG is ~2MB,
# which stalls that first load, so the web copy is downscaled and (if needed)
# palette-quantised until it fits a small byte budget while staying a PNG.
WEB_COVER_MAX_BYTES = 200_000


def save_web_cover(
    image: Image.Image, path: Path, max_bytes: int = WEB_COVER_MAX_BYTES
) -> int:
    """Write a load-friendly PNG copy of a cover under ``max_bytes``.

    The full-resolution frame is kept in memory for the video pipeline; only the
    uploaded web asset is shrunk. We try progressively smaller widths, first as an
    optimised true-colour PNG and then with decreasing palette depth, returning as
    soon as the encoded size fits the budget so quality is only traded when needed.
    """

    source = image.convert("RGB")
    encoded: bytes | None = None
    for width in (720, 640, 540, 480, 420, 360, 320):
        if width >= source.width:
            resized = source
        else:
            height = max(1, round(source.height * width / source.width))
            resized = source.resize((width, height), Image.LANCZOS)

        buffer = BytesIO()
        resized.save(buffer, format="PNG", optimize=True)
        encoded = buffer.getvalue()
        if len(encoded) <= max_bytes:
            path.write_bytes(encoded)
            return len(encoded)

        for colors in (256, 192, 128, 96, 64, 48):
            quantized = resized.quantize(
                colors=colors, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG
            )
            buffer = BytesIO()
            quantized.save(buffer, format="PNG", optimize=True)
            encoded = buffer.getvalue()
            if len(encoded) <= max_bytes:
                path.write_bytes(encoded)
                return len(encoded)

    # Smallest attempt still exceeded the budget; persist it anyway so the run
    # does not fail on an unusually incompressible frame.
    assert encoded is not None
    path.write_bytes(encoded)
    return len(encoded)

# A CJK face is listed as the last resort for both languages because it also
# carries Latin glyphs, while a Latin-only face renders Chinese as tofu boxes.
_CJK_FONT_CANDIDATES = (
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
)
_LATIN_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)

_MARGIN = 72
_HEADING_SIZE = 62
_POINT_SIZE = 46
_SUBTITLE_SIZE = 48


def _first_existing(candidates: tuple[str, ...]) -> str | None:
    return next((path for path in candidates if Path(path).exists()), None)


class FrameRenderer:
    """Composites headings, key points and captions onto generated backgrounds."""

    def __init__(self, font_cn: str | None = None, font_en: str | None = None) -> None:
        resolved_cn = font_cn or os.getenv("MEDIA_FONT_CN") or _first_existing(_CJK_FONT_CANDIDATES)
        if resolved_cn is None:
            raise RuntimeError(
                "no CJK font found; set MEDIA_FONT_CN to a .ttc/.ttf that covers Chinese"
            )
        resolved_en = (
            font_en
            or os.getenv("MEDIA_FONT_EN")
            or _first_existing(_LATIN_FONT_CANDIDATES)
            or resolved_cn
        )
        self._fonts = {"cn": resolved_cn, "en": resolved_en}
        self._cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    def _font(self, language: str, size: int) -> ImageFont.FreeTypeFont:
        key = (language, size)
        if key not in self._cache:
            self._cache[key] = ImageFont.truetype(self._fonts[language], size)
        return self._cache[key]

    @staticmethod
    def _wrap(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont,
        max_width: int,
        language: str,
    ) -> list[str]:
        # Chinese has no inter-word spaces, so it wraps per character; English
        # wraps per word to avoid splitting words mid-glyph.
        tokens = list(text) if language == "cn" else text.split()
        joiner = "" if language == "cn" else " "
        lines: list[str] = []
        current = ""
        for token in tokens:
            candidate = f"{current}{joiner}{token}" if current else token
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = token
        if current:
            lines.append(current)
        return lines or [""]

    @staticmethod
    def _panel(overlay: ImageDraw.ImageDraw, box: tuple[int, int, int, int], alpha: int) -> None:
        overlay.rounded_rectangle(box, radius=28, fill=(12, 14, 20, alpha))

    def prepare_page(self, artwork: Path) -> Image.Image:
        """Return the still frame for one page.

        The headline and key points are painted by the image model as part of the
        artwork, so nothing is composited here; this only normalises whatever the
        model returned to the 1080x1920 frame. Spoken-line subtitles are still
        burned in afterwards by :meth:`render_caption_frames`.
        """
        return self._cover(Image.open(artwork).convert("RGB"))

    def render_caption_frames(
        self, page: Image.Image, lines: list[str], language: str, output_dir: Path, stem: str
    ) -> list[Path]:
        """Burn one caption line per frame, returning the frames in play order."""
        output_dir.mkdir(parents=True, exist_ok=True)
        frames: list[Path] = []
        for index, line in enumerate(lines):
            frame = self._with_caption(page, line, language)
            path = output_dir / f"{stem}-{index:02d}.png"
            frame.save(path, format="PNG")
            frames.append(path)
        return frames

    def _with_caption(self, page: Image.Image, line: str, language: str) -> Image.Image:
        canvas = page.convert("RGBA")
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        font = self._font(language, _SUBTITLE_SIZE)
        text_width = FRAME_WIDTH - 2 * _MARGIN - 48
        caption_lines = self._wrap(draw, line, font, text_width, language)
        line_height = int(_SUBTITLE_SIZE * 1.42)
        panel_height = len(caption_lines) * line_height + 56
        bottom = FRAME_HEIGHT - 150
        top = bottom - panel_height
        self._panel(draw, (_MARGIN, top, FRAME_WIDTH - _MARGIN, bottom), 205)

        cursor = top + 28
        for caption in caption_lines:
            width = draw.textlength(caption, font=font)
            draw.text(
                ((FRAME_WIDTH - width) / 2, cursor),
                caption,
                font=font,
                fill=(255, 255, 255, 255),
            )
            cursor += line_height
        return Image.alpha_composite(canvas, layer).convert("RGB")

    @staticmethod
    def _cover(image: Image.Image) -> Image.Image:
        """Scale to fill 1080x1920 and centre-crop, mirroring ffmpeg's crop filter."""
        scale = max(FRAME_WIDTH / image.width, FRAME_HEIGHT / image.height)
        resized = image.resize(
            (round(image.width * scale), round(image.height * scale)), Image.LANCZOS
        )
        left = (resized.width - FRAME_WIDTH) // 2
        top = (resized.height - FRAME_HEIGHT) // 2
        return resized.crop((left, top, left + FRAME_WIDTH, top + FRAME_HEIGHT))
