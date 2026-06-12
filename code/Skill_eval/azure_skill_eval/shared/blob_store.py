"""Azure Blob Storage helper for evaluation artifacts.

Layout in the configured container:

    runs.json                            # global index, newest first
    <run_id>/index.json                  # per-run file index
    <run_id>/summary.json                # per-run aggregate
    <run_id>/<case>__<model>.json        # per-case record

A run_id is ``yymmdd-XXXXXX`` (6 lowercase hex chars).
"""
from __future__ import annotations

import datetime as _dt
import json
import uuid
from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient, ContainerClient

from .config import storage_account_url, storage_container_name


def new_run_id() -> str:
    stamp = _dt.datetime.now().strftime("%y%m%d")
    return f"{stamp}-{uuid.uuid4().hex[:6]}"


class BlobStore:
    def __init__(self) -> None:
        self._account_url = storage_account_url()
        self._container = storage_container_name()
        self._credential: DefaultAzureCredential | None = None
        self._service: BlobServiceClient | None = None

    async def __aenter__(self) -> "BlobStore":
        self._credential = DefaultAzureCredential()
        self._service = BlobServiceClient(
            account_url=self._account_url, credential=self._credential
        )
        await self._ensure_container()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._service is not None:
            await self._service.close()
        if self._credential is not None:
            await self._credential.close()
        self._service = None
        self._credential = None

    def _container_client(self) -> ContainerClient:
        assert self._service is not None, "BlobStore must be used as async context manager"
        return self._service.get_container_client(self._container)

    async def _ensure_container(self) -> None:
        cc = self._container_client()
        try:
            await cc.get_container_properties()
        except ResourceNotFoundError:
            await cc.create_container()

    async def upload_json(self, blob_path: str, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        cc = self._container_client()
        bc = cc.get_blob_client(blob_path)
        await bc.upload_blob(data, overwrite=True, content_type="application/json")

    async def download_json(self, blob_path: str) -> Any | None:
        cc = self._container_client()
        bc = cc.get_blob_client(blob_path)
        try:
            stream = await bc.download_blob()
            data = await stream.readall()
        except ResourceNotFoundError:
            return None
        return json.loads(data.decode("utf-8"))

    async def exists(self, blob_path: str) -> bool:
        cc = self._container_client()
        bc = cc.get_blob_client(blob_path)
        try:
            await bc.get_blob_properties()
            return True
        except ResourceNotFoundError:
            return False

    async def list_run_files(self, run_id: str) -> list[str]:
        cc = self._container_client()
        prefix = f"{run_id}/"
        names: list[str] = []
        async for blob in cc.list_blobs(name_starts_with=prefix):
            names.append(blob.name[len(prefix):])
        return sorted(names)

    async def list_runs(self) -> list[dict[str, Any]]:
        existing = await self.download_json("runs.json")
        return list(existing) if isinstance(existing, list) else []

    async def prepend_run(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        runs = await self.list_runs()
        runs = [r for r in runs if r.get("run_id") != entry.get("run_id")]
        runs.insert(0, entry)
        await self.upload_json("runs.json", runs)
        return runs
