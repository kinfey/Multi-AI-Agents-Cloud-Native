import os
from functools import lru_cache
from typing import Literal

from azure.core.credentials import TokenCredential
from azure.core.pipeline.transport import RequestsTransport
from azure.identity import (
    DefaultAzureCredential,
    ManagedIdentityCredential,
    WorkloadIdentityCredential,
)
from pydantic import HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    azure_client_id: str | None = None
    azure_storage_account_url: HttpUrl
    azure_storage_container: str = "media"
    azure_foundry_project_endpoint: HttpUrl
    azure_text_model: str = "gpt-5.5"
    azure_image_endpoint: HttpUrl
    azure_image_model: str = "MAI-Image-2.5-Pro"
    azure_image_api_key: str
    azure_speech_endpoint: HttpUrl
    azure_speech_key: str
    azure_speech_voice: str = "zh-CN-Mei"
    azure_speech_voice_en: str = "en-US-AvaMultilingualNeural"
    # MAI voice is the primary narration route; keep `azure-speech` as a
    # fallback backend for environments that do not expose MAI-Voice-2.
    tts_backend: Literal["azure-speech", "mai-voice"] = "mai-voice"
    azure_voice_model: str = "MAI-Voice-2"
    azure_voice_name_cn: str = "zh-CN-Mei"
    azure_voice_name_en: str = "en-US-Olivia"
    media_font_cn: str | None = None
    media_font_en: str | None = None
    news_feed_urls: str

    @property
    def feeds(self) -> list[str]:
        return [url.strip() for url in self.news_feed_urls.split(",") if url.strip()]

    def credential(self) -> TokenCredential:
        token_file = os.getenv("AZURE_FEDERATED_TOKEN_FILE")
        tenant_id = os.getenv("AZURE_TENANT_ID")
        if self.azure_client_id and token_file and tenant_id:
            transport = RequestsTransport(connection_timeout=10, read_timeout=30)
            return WorkloadIdentityCredential(
                tenant_id=tenant_id,
                client_id=self.azure_client_id,
                token_file_path=token_file,
                transport=transport,
            )
        if self.azure_client_id:
            return ManagedIdentityCredential(client_id=self.azure_client_id)
        return DefaultAzureCredential(exclude_interactive_browser_credential=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
