from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator

Language = Literal["cn", "en"]
LANGUAGES: tuple[Language, ...] = ("cn", "en")


class Source(BaseModel):
    title: str
    url: HttpUrl
    published_at: datetime | None = None


class Story(BaseModel):
    position: int = Field(ge=1, le=10)
    market: Literal["CN", "US"]
    title: str = Field(min_length=4, max_length=80)
    title_en: str = Field(min_length=4, max_length=120)
    summary: str = Field(min_length=10, max_length=500)
    summary_en: str = Field(min_length=10, max_length=600)
    narration: list[str] = Field(min_length=4, max_length=5)
    narration_en: list[str] = Field(min_length=4, max_length=5)
    key_points: list[str] = Field(min_length=2, max_length=3)
    key_points_en: list[str] = Field(min_length=2, max_length=3)
    image_prompt: str = Field(min_length=20)
    sources: list[Source] = Field(min_length=1)

    def title_for(self, language: Language) -> str:
        return self.title if language == "cn" else self.title_en

    def narration_for(self, language: Language) -> list[str]:
        return self.narration if language == "cn" else self.narration_en

    def key_points_for(self, language: Language) -> list[str]:
        return self.key_points if language == "cn" else self.key_points_en


class LanguageAssets(BaseModel):
    cover_image: str
    story_images: list[str]
    end_image: str
    cover_audio: str
    story_audio: list[str]
    end_audio: str
    video: str

    @classmethod
    def for_date(cls, run_date: date, language: Language) -> LanguageAssets:
        prefix = run_date.strftime("%y%m%d")
        numbers = [f"{position:02d}" for position in range(1, 11)]
        return cls(
            cover_image=f"{prefix}/imgs/{language}/cover.png",
            story_images=[f"{prefix}/imgs/{language}/{number}.png" for number in numbers],
            end_image=f"{prefix}/imgs/{language}/end.png",
            cover_audio=f"{prefix}/audio/{language}/cover.wav",
            story_audio=[f"{prefix}/audio/{language}/{number}.wav" for number in numbers],
            end_audio=f"{prefix}/audio/{language}/end.wav",
            video=f"{prefix}/video/final_{language}.mp4",
        )

    @property
    def images(self) -> list[str]:
        return [self.cover_image, *self.story_images, self.end_image]

    @property
    def audio(self) -> list[str]:
        return [self.cover_audio, *self.story_audio, self.end_audio]


class AssetPaths(BaseModel):
    cn: LanguageAssets
    en: LanguageAssets

    @classmethod
    def for_date(cls, run_date: date) -> AssetPaths:
        return cls(
            cn=LanguageAssets.for_date(run_date, "cn"),
            en=LanguageAssets.for_date(run_date, "en"),
        )

    def for_language(self, language: Language) -> LanguageAssets:
        return self.cn if language == "cn" else self.en


class DailyManifest(BaseModel):
    run_date: date
    generated_at: datetime
    title: str
    title_en: str
    stories: list[Story]
    assets: AssetPaths
    duration_seconds: dict[Language, float] = Field(default_factory=dict)
    status: Literal["processing", "ready", "failed"] = "processing"

    def title_for(self, language: Language) -> str:
        return self.title if language == "cn" else self.title_en

    @model_validator(mode="after")
    def validate_story_sequence(self) -> DailyManifest:
        if len(self.stories) != 10:
            raise ValueError("a daily manifest must contain exactly 10 stories")
        if [story.position for story in self.stories] != list(range(1, 11)):
            raise ValueError("story positions must be sequential from 1 through 10")
        return self
