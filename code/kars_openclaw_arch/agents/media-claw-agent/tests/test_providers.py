from types import SimpleNamespace

import httpx
from media_claw_agent import providers


def test_news_provider_fetches_with_timeout_and_parses_response_bytes(monkeypatch) -> None:
    requests = []

    def fake_get(url: str, **kwargs) -> httpx.Response:
        requests.append((url, kwargs))
        return httpx.Response(200, content=b"feed", request=httpx.Request("GET", url))

    monkeypatch.setattr(providers.httpx, "get", fake_get)
    monkeypatch.setattr(
        providers.feedparser,
        "parse",
        lambda content: SimpleNamespace(
            entries=[{"title": "Market", "link": "https://example.com/story"}]
        ),
    )

    articles = providers.NewsProvider(["https://example.com/feed"]).fetch()

    assert articles == [
        {
            "title": "Market",
            "summary": "",
            "url": "https://example.com/story",
            "published": "",
        }
    ]
    assert requests == [
        (
            "https://example.com/feed",
            {
                "headers": providers.NewsProvider.FEED_HEADERS,
                "follow_redirects": True,
                "timeout": 30,
            },
        )
    ]


def test_news_provider_continues_after_one_feed_fails(monkeypatch) -> None:
    def fake_get(url: str, **kwargs) -> httpx.Response:
        if "failed" in url:
            raise httpx.ConnectTimeout("timed out")
        return httpx.Response(200, content=b"feed", request=httpx.Request("GET", url))

    monkeypatch.setattr(providers.httpx, "get", fake_get)
    monkeypatch.setattr(
        providers.feedparser,
        "parse",
        lambda content: SimpleNamespace(
            entries=[{"title": "Market", "link": "https://example.com/story"}]
        ),
    )

    articles = providers.NewsProvider(
        ["https://failed.example/feed", "https://example.com/feed"]
    ).fetch()

    assert len(articles) == 1


def test_mai_voice_uses_speech_sdk_endpoint_and_model_qualified_voices(
    monkeypatch,
) -> None:
    created = []

    class FakeSpeechConfig:
        def __init__(self, subscription: str, endpoint: str) -> None:
            self.subscription = subscription
            self.endpoint = endpoint
            self.speech_synthesis_voice_name = ""
            self.output_format = None
            self.proxy = None
            created.append(self)

        def set_speech_synthesis_output_format(self, output_format) -> None:
            self.output_format = output_format

        def set_proxy(self, hostname: str, port: int) -> None:
            self.proxy = (hostname, port)

    monkeypatch.setattr(providers.speechsdk, "SpeechConfig", FakeSpeechConfig)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8444")
    settings = SimpleNamespace(
        azure_speech_endpoint=(
            "https://example-speech.cognitiveservices.azure.com/speech/path"
        ),
        azure_speech_key="speech-key",
        azure_voice_model="MAI-Voice-2",
        azure_voice_name_cn="zh-CN-Mei",
        azure_voice_name_en="en-US-Olivia",
    )

    providers.MaiVoiceSpeechProvider(settings)  # type: ignore[arg-type]

    assert [config.endpoint for config in created] == [
        "https://example-speech.cognitiveservices.azure.com",
        "https://example-speech.cognitiveservices.azure.com",
    ]
    assert [config.subscription for config in created] == [
        "speech-key",
        "speech-key",
    ]
    assert [config.speech_synthesis_voice_name for config in created] == [
        "zh-CN-Mei:MAI-Voice-2",
        "en-US-Olivia:MAI-Voice-2",
    ]
    assert [config.proxy for config in created] == [
        ("127.0.0.1", 8444),
        ("127.0.0.1", 8444),
    ]