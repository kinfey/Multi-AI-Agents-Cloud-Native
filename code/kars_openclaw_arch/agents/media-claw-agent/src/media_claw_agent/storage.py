from __future__ import annotations

from pathlib import Path

from azure.core.credentials import TokenCredential
from azure.storage.blob import BlobServiceClient, ContentSettings


class MediaStorage:
    # The composed mp4 is tens of MB. The SDK default `max_single_put_size` is
    # 64 MiB, so the whole video went up as ONE streaming PUT: a stall anywhere
    # in that request killed the entire upload with
    # `ServiceResponseError: ('Connection aborted.', TimeoutError(...))`, and
    # because the pipeline builds the video in a TemporaryDirectory the render
    # was discarded with it. Chunking into 8 MiB blocks means each block is an
    # independently retryable request, so a transient stall costs one block
    # instead of the whole run.
    MAX_SINGLE_PUT_SIZE = 8 * 1024 * 1024
    MAX_BLOCK_SIZE = 8 * 1024 * 1024
    CONNECTION_TIMEOUT = 10
    READ_TIMEOUT = 30
    RETRY_TOTAL = 2
    MAX_CONCURRENCY = 4

    def __init__(
        self,
        account_url: str,
        container_name: str,
        credential: TokenCredential,
    ) -> None:
        service = BlobServiceClient(
            account_url=account_url,
            credential=credential,
            max_single_put_size=self.MAX_SINGLE_PUT_SIZE,
            max_block_size=self.MAX_BLOCK_SIZE,
            connection_timeout=self.CONNECTION_TIMEOUT,
            read_timeout=self.READ_TIMEOUT,
            retry_total=self.RETRY_TOTAL,
        )
        self._container = service.get_container_client(container_name)

    def upload(self, source: Path, blob_name: str, content_type: str) -> None:
        with source.open("rb") as stream:
            self._container.upload_blob(
                name=blob_name,
                data=stream,
                overwrite=True,
                max_concurrency=self.MAX_CONCURRENCY,
                content_settings=ContentSettings(content_type=content_type),
            )

    def upload_json(self, data: str, blob_name: str) -> None:
        self._container.upload_blob(
            name=blob_name,
            data=data.encode("utf-8"),
            overwrite=True,
            content_settings=ContentSettings(content_type="application/json; charset=utf-8"),
        )

    def download(self, blob_name: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as stream:
            self._container.download_blob(blob_name).readinto(stream)

    def download_text(self, blob_name: str) -> str:
        return self._container.download_blob(blob_name).readall().decode("utf-8")
