from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

import azure.cognitiveservices.speech as speechsdk
import feedparser
import httpx
import structlog
from azure.ai.projects import AIProjectClient
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from .config import Settings
from .models import Language, Source, Story

logger = structlog.get_logger(__name__)


class NewsProvider:
    FEED_TIMEOUT_SECONDS = 30
    FEED_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    }

    def __init__(self, feed_urls: list[str]) -> None:
        self._feed_urls = feed_urls

    def fetch(self, limit_per_feed: int = 25) -> list[dict[str, Any]]:
        articles: list[dict[str, Any]] = []
        for feed_url in self._feed_urls:
            logger.info("news_feed_fetch_started", url=feed_url)
            try:
                response = httpx.get(
                    feed_url,
                    headers=self.FEED_HEADERS,
                    follow_redirects=True,
                    timeout=self.FEED_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
            except httpx.HTTPError as error:
                logger.warning("news_feed_fetch_failed", url=feed_url, error=str(error))
                continue

            feed = feedparser.parse(response.content)
            logger.info("news_feed_fetch_completed", url=feed_url, entries=len(feed.entries))
            for entry in feed.entries[:limit_per_feed]:
                articles.append(
                    {
                        "title": entry.get("title", ""),
                        "summary": entry.get("summary", ""),
                        "url": entry.get("link", ""),
                        "published": entry.get("published", ""),
                    }
                )
        if not articles:
            raise RuntimeError("all configured news feeds failed or returned no articles")
        return articles


class FoundryEditorialProvider:
    def __init__(self, settings: Settings) -> None:
        project = AIProjectClient(
            endpoint=str(settings.azure_foundry_project_endpoint),
            credential=settings.credential(),
        )
        self._client = project.get_openai_client()
        self._model = settings.azure_text_model

    @retry(stop=stop_after_attempt(3), wait=wait_exponential_jitter(initial=1, max=20))
    def select_stories(self, candidates: list[dict[str, Any]]) -> list[Story]:
        response = self._client.responses.create(
            model=self._model,
            # Bound the call explicitly. The openai SDK defaults to a 600s timeout
            # with its own internal retries, so a server-side stall silently turns
            # into a ~30 minute hang with no output. A stalled request was observed
            # sitting on an ESTABLISHED socket for 11+ minutes while a healthy run
            # of the same prompt finishes in under 2. Failing at 180s lets the
            # tenacity retry above issue a fresh request instead.
            timeout=180,
            # `type: "message"` must be explicit. With two or more input items the
            # Foundry Responses endpoint rejects any item that omits it, reporting
            # `Invalid value: ''` for input[1]. A single item is accepted without it,
            # which is why this only shows up on the real two-message call.
            input=[
                {
                    "type": "message",
                    "role": "system",
                    "content": (
                        "你是严谨的财经主编，负责双语日播节目《盘面十条》(英文名 Market Ten)。"
                        "仅从候选资料选择当天最重要的10条中美股市动态，中国和美国市场均需覆盖。"
                        "每条都要输出中英文两套内容：中文4到5句配音(narration)，英文4到5句配音"
                        "(narration_en)，两者信息完全对应；中英文标题(title/title_en)与摘要"
                        "(summary/summary_en)；以及2到3条用于画面的关键要点(key_points/"
                        "key_points_en)，每条不超过14个汉字或8个英文单词。"
                        "配音需要承前启后：每条以自然的过渡语衔接上一条，结尾自然引向下一条，"
                        "但不要自行添加开场问候或结束语，节目的开场与结尾由系统统一生成。"
                        "不得编造价格、涨跌幅、引语或来源。"
                        "image_prompt 使用英文，描述统一专业财经编辑风格的9:16竖版背景画面，"
                        "并且必须明确要求画面中不出现任何文字、字母、数字或水印，"
                        "因为标题与要点会在后期叠加。"
                    ),
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": json.dumps(candidates, ensure_ascii=False),
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "daily_finance_stories",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["stories"],
                        "properties": {
                            "stories": {
                                "type": "array",
                                "minItems": 10,
                                "maxItems": 10,
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "position",
                                        "market",
                                        "title",
                                        "title_en",
                                        "summary",
                                        "summary_en",
                                        "narration",
                                        "narration_en",
                                        "key_points",
                                        "key_points_en",
                                        "image_prompt",
                                        "sources",
                                    ],
                                    "properties": {
                                        "position": {
                                            "type": "integer",
                                            "minimum": 1,
                                            "maximum": 10,
                                        },
                                        "market": {"type": "string", "enum": ["CN", "US"]},
                                        "title": {"type": "string"},
                                        "title_en": {"type": "string"},
                                        "summary": {"type": "string"},
                                        "summary_en": {"type": "string"},
                                        "narration": {
                                            "type": "array",
                                            "minItems": 4,
                                            "maxItems": 5,
                                            "items": {"type": "string"},
                                        },
                                        "narration_en": {
                                            "type": "array",
                                            "minItems": 4,
                                            "maxItems": 5,
                                            "items": {"type": "string"},
                                        },
                                        "key_points": {
                                            "type": "array",
                                            "minItems": 2,
                                            "maxItems": 3,
                                            "items": {"type": "string"},
                                        },
                                        "key_points_en": {
                                            "type": "array",
                                            "minItems": 2,
                                            "maxItems": 3,
                                            "items": {"type": "string"},
                                        },
                                        "image_prompt": {"type": "string"},
                                        "sources": {
                                            "type": "array",
                                            "minItems": 1,
                                            "items": {
                                                "type": "object",
                                                "additionalProperties": False,
                                                "required": ["title", "url"],
                                                "properties": {
                                                    "title": {"type": "string"},
                                                    "url": {"type": "string"},
                                                },
                                            },
                                        },
                                    },
                                },
                            }
                        },
                    },
                }
            },
        )
        payload = json.loads(response.output_text)
        return [Story.model_validate(item) for item in payload["stories"]]


class ImageProvider:
    # MAI-Image-2.5-Pro constrains every request to: each dimension >= 768, and
    # width * height <= 1_048_576. The portrait 1024x1792 used previously is
    # 1_835_008 pixels and was rejected with `unsupported_request_value`.
    #
    # 768x1360 is the tallest 16-aligned size that stays inside the budget:
    #   768 * 1360 = 1_044_480 <= 1_048_576, and both sides clear the 768 floor.
    # Its 1:1.7708 ratio is visually 9:16 (true 9:16 is 1:1.7778).
    #
    # Do not "improve" this to 768x1365 to hit the exact ratio: the service
    # silently snaps dimensions down to a multiple of 16, so a 1365 request
    # returns a 1360 image anyway. Requesting 1360 keeps the request and the
    # decoded PNG identical, which matters because VideoComposer assumes every
    # frame shares one resolution.
    IMAGE_WIDTH = 768
    IMAGE_HEIGHT = 1360

    def __init__(self, settings: Settings) -> None:
        self._endpoint = str(settings.azure_image_endpoint)
        self._model = settings.azure_image_model
        self._headers = {"api-key": settings.azure_image_api_key}

    @retry(stop=stop_after_attempt(3), wait=wait_exponential_jitter(initial=1, max=20))
    def generate(self, prompt: str, output_path: Path) -> None:
        response = httpx.post(
            self._endpoint,
            headers=self._headers,
            json={
                "prompt": prompt,
                "width": self.IMAGE_WIDTH,
                "height": self.IMAGE_HEIGHT,
                "model": self._model,
            },
            timeout=180,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            detail = response.text[:2000]
            raise RuntimeError(
                f"image generation failed ({response.status_code}): {detail}"
            ) from error
        output_path.write_bytes(base64.b64decode(response.json()["data"][0]["b64_json"]))


class AzureNeuralSpeechProvider:
    """Bilingual TTS over the Azure Speech SDK's neural voices."""

    def __init__(self, settings: Settings) -> None:
        self._configs = {
            "cn": self._build(settings, settings.azure_speech_voice),
            "en": self._build(settings, settings.azure_speech_voice_en),
        }

    @staticmethod
    def _build(settings: Settings, voice: str) -> speechsdk.SpeechConfig:
        config = speechsdk.SpeechConfig(
            subscription=settings.azure_speech_key,
            endpoint=str(settings.azure_speech_endpoint),
        )
        config.speech_synthesis_voice_name = voice
        config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
        )
        return config

    @retry(stop=stop_after_attempt(3), wait=wait_exponential_jitter(initial=1, max=20))
    def synthesize(self, text: str, output_path: Path, language: Language = "cn") -> None:
        audio = speechsdk.audio.AudioOutputConfig(filename=str(output_path))
        # `synthesizer` MUST stay bound to a local for the whole call. Chaining
        # `SpeechSynthesizer(...).speak_text_async(text).get()` leaves the
        # synthesizer as a temporary: CPython drops its last reference as soon as
        # the expression yields the future, so the native object is destroyed
        # while the SDK's background thread is still writing the WAV. That is a
        # use-after-free and it crashed the interpreter with SIGSEGV inside
        # CSpxSynthesizer::ExecuteSynthesis (ostream::flush on a freed stream).
        # The failure is a race, so it only showed up part-way through a run.
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=self._configs[language], audio_config=audio
        )
        result = synthesizer.speak_text_async(text).get()
        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            details = result.cancellation_details
            message = f"speech synthesis failed: {details.reason}: {details.error_details}"
            raise RuntimeError(message)


class MaiVoiceSpeechProvider:
    """Bilingual MAI-Voice-2 TTS over the Azure Speech SDK."""

    def __init__(self, settings: Settings) -> None:
        parsed = urlparse(str(settings.azure_speech_endpoint))
        endpoint = f"{parsed.scheme}://{parsed.netloc}"
        self._model = settings.azure_voice_model
        self._speakers = {
            "cn": settings.azure_voice_name_cn,
            "en": settings.azure_voice_name_en,
        }
        self._configs = {
            "cn": self._build(settings, endpoint, settings.azure_voice_name_cn),
            "en": self._build(settings, endpoint, settings.azure_voice_name_en),
        }

    @staticmethod
    def _build(
        settings: Settings, endpoint: str, speaker: str
    ) -> speechsdk.SpeechConfig:
        config = speechsdk.SpeechConfig(
            subscription=settings.azure_speech_key,
            endpoint=endpoint,
        )
        config.speech_synthesis_voice_name = f"{speaker}:{settings.azure_voice_model}"
        config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
        )
        proxy = urlparse(os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or "")
        if proxy.hostname and proxy.port:
            config.set_proxy(proxy.hostname, proxy.port)
        return config

    @retry(stop=stop_after_attempt(3), wait=wait_exponential_jitter(initial=1, max=20))
    def synthesize(self, text: str, output_path: Path, language: Language = "cn") -> None:
        started = perf_counter()
        logger.info(
            "mai_voice_request_started",
            language=language,
            speaker=self._speakers[language],
            model=self._model,
            characters=len(text),
        )
        audio = speechsdk.audio.AudioOutputConfig(filename=str(output_path))
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=self._configs[language], audio_config=audio
        )
        result = synthesizer.speak_text_async(text).get()
        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            details = result.cancellation_details
            logger.error(
                "mai_voice_request_cancelled",
                language=language,
                speaker=self._speakers[language],
                model=self._model,
                reason=str(details.reason),
                error_details=details.error_details,
                elapsed_seconds=round(perf_counter() - started, 3),
            )
            message = f"speech synthesis failed: {details.reason}: {details.error_details}"
            raise RuntimeError(message)
        logger.info(
            "mai_voice_request_completed",
            language=language,
            speaker=self._speakers[language],
            model=self._model,
            bytes=output_path.stat().st_size,
            elapsed_seconds=round(perf_counter() - started, 3),
        )


def build_speech_provider(settings: Settings) -> AzureNeuralSpeechProvider | MaiVoiceSpeechProvider:
    if settings.tts_backend == "mai-voice":
        return MaiVoiceSpeechProvider(settings)
    return AzureNeuralSpeechProvider(settings)


def sources_from_candidates(candidates: list[dict[str, Any]]) -> list[Source]:
    return [
        Source(
            title=item["title"],
            url=item["url"],
            published_at=datetime.now(UTC),
        )
        for item in candidates
        if item.get("title") and item.get("url")
    ]
