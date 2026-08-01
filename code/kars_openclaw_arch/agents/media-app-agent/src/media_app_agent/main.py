from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Literal
from urllib.parse import urlparse

from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient, generate_blob_sas
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

REACTION_PREFIX = "reactions"
CATALOG_CACHE_SECONDS = 60


class Reaction(BaseModel):
    kind: Literal["like", "star"]
    value: int = Field(default=1, ge=1, le=5)


class Repository:
    def __init__(self) -> None:
        account_url = os.environ["AZURE_STORAGE_ACCOUNT_URL"]
        client_id = os.getenv("AZURE_CLIENT_ID")
        credential = (
            ManagedIdentityCredential(client_id=client_id)
            if client_id
            else DefaultAzureCredential(exclude_interactive_browser_credential=True)
        )
        self._service = BlobServiceClient(account_url, credential=credential)
        self._container = self._service.get_container_client(
            os.getenv("AZURE_STORAGE_CONTAINER", "media")
        )
        self._account_name = urlparse(account_url).netloc.split(".")[0]
        self._credential = credential
        self._catalog_cache: list[dict] | None = None
        self._catalog_cached_at = 0.0
        self._catalog_lock = Lock()

    def videos(self) -> list[dict]:
        now = monotonic()
        if (
            self._catalog_cache is not None
            and now - self._catalog_cached_at < CATALOG_CACHE_SECONDS
        ):
            return deepcopy(self._catalog_cache)

        with self._catalog_lock:
            now = monotonic()
            if (
                self._catalog_cache is not None
                and now - self._catalog_cached_at < CATALOG_CACHE_SECONDS
            ):
                return deepcopy(self._catalog_cache)
            catalog = self._load_videos()
            self._catalog_cache = catalog
            self._catalog_cached_at = now
            return deepcopy(catalog)

    def _load_videos(self) -> list[dict]:
        manifests: list[dict] = []
        # One delegation key is reused for every SAS in this response. Minting it
        # per blob costs a round trip each, and a bilingual manifest needs four.
        delegation_key = self._delegation_key()
        for blob in self._container.list_blobs(name_starts_with=""):
            if blob.name.endswith("/manifest.json"):
                manifest = json.loads(self._container.download_blob(blob.name).readall())
                if manifest.get("status") == "ready":
                    manifest["languages"] = {
                        language: {
                            "playback_url": self._read_url(assets["video"], delegation_key),
                            "cover_url": self._read_url(assets["cover_image"], delegation_key),
                        }
                        for language, assets in self._language_assets(manifest).items()
                    }
                    default = manifest["languages"].get("cn") or next(
                        iter(manifest["languages"].values())
                    )
                    manifest["playback_url"] = default["playback_url"]
                    manifest["cover_url"] = default["cover_url"]
                    manifests.append(manifest)
        return sorted(manifests, key=lambda item: item["run_date"], reverse=True)

    @staticmethod
    def _language_assets(manifest: dict) -> dict[str, dict]:
        """Normalise both manifest shapes to `{language: assets}`.

        Episodes published before the bilingual pipeline store a single flat
        asset block; treating those as Chinese-only keeps the back catalogue
        playable instead of 500-ing on a missing key.
        """
        assets = manifest["assets"]
        if "cn" in assets:
            return {language: assets[language] for language in ("cn", "en") if language in assets}
        return {"cn": assets}

    def record_reaction(self, video_date: str, reaction: Reaction, voter: str) -> bool:
        """Persist one vote, returning False when this voter already reacted.

        The voter id is part of the blob name and the upload runs with
        `overwrite=False`, which the SDK sends as `If-None-Match: *`. That makes
        "one vote per address" an atomic create-if-absent in storage, so two
        concurrent taps cannot both win the way a read-then-write check would.
        """
        self._require_date(video_date)
        blob = self._container.get_blob_client(
            f"{video_date}/{REACTION_PREFIX}/{reaction.kind}/{voter}.json"
        )
        record = {
            "created_at": datetime.now(UTC).isoformat(),
            "kind": reaction.kind,
            "value": reaction.value,
        }
        try:
            blob.upload_blob(
                json.dumps(record).encode(),
                overwrite=False,
                # Mirrored into metadata so the summary can total the scores
                # from a single list call instead of downloading every vote.
                metadata={"value": str(reaction.value)},
            )
        except ResourceExistsError:
            return False
        return True

    def reaction_summary(self, video_date: str, voter: str) -> dict:
        self._require_date(video_date)
        prefix = f"{video_date}/{REACTION_PREFIX}/"
        likes = 0
        score_total = 0
        votes = 0
        mine = {"like": False, "star": 0}
        for blob in self._container.list_blobs(
            name_starts_with=prefix, include=["metadata"]
        ):
            parts = blob.name[len(prefix) :].split("/")
            if len(parts) != 2:
                continue
            kind, owner = parts[0], parts[1].removesuffix(".json")
            if kind == "like":
                likes += 1
                if owner == voter:
                    mine["like"] = True
            elif kind == "star":
                value = int((blob.metadata or {}).get("value") or 0)
                if not 1 <= value <= 5:
                    continue
                score_total += value
                votes += 1
                if owner == voter:
                    mine["star"] = value
        return {
            "likes": likes,
            "stars": {
                "votes": votes,
                "total": score_total,
                "average": round(score_total / votes, 1) if votes else 0.0,
            },
            "mine": mine,
        }

    @staticmethod
    def _require_date(video_date: str) -> None:
        if not video_date.isdigit() or len(video_date) != 6:
            raise ValueError("video date must use yymmdd")

    def _delegation_key(self):  # noqa: ANN202 - SDK model type
        now = datetime.now(UTC)
        return self._service.get_user_delegation_key(now, now + timedelta(hours=1))

    def _read_url(self, blob_name: str, delegation_key) -> str:  # noqa: ANN001
        token = generate_blob_sas(
            account_name=self._account_name,
            container_name=self._container.container_name,
            blob_name=blob_name,
            user_delegation_key=delegation_key,
            permission="r",
            expiry=datetime.now(UTC) + timedelta(minutes=30),
        )
        return f"{self._container.url}/{blob_name}?{token}"


app = FastAPI(title="Media App Agent", version="1.0.0")
app.add_middleware(GZipMiddleware, minimum_size=1024)
repository: Repository | None = None


@app.middleware("http")
async def revalidate_static_assets(request: Request, call_next):
    # Static HTML/JS/CSS must always be revalidated so a redeploy is picked up
    # immediately instead of being served from a stale browser cache. StaticFiles
    # emits an ETag, so revalidation returns a cheap 304 when nothing changed.
    response = await call_next(request)
    if request.method in {"GET", "HEAD"} and not request.url.path.startswith(
        ("/api/", "/health")
    ):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


def get_repository() -> Repository:
    global repository
    if repository is None:
        repository = Repository()
    return repository


def voter_id(request: Request) -> str:
    """Derive a stable, non-reversible id for the calling address.

    Container Apps terminates TLS at its ingress, so `request.client` is always
    the proxy; the caller only survives in `X-Forwarded-For`. That header is
    client-supplied and therefore spoofable -- this is a civility guard against
    accidental double taps, not an authentication boundary. The address is
    salted and hashed so no raw IP is ever written to storage.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    address = forwarded.split(",")[0].strip()
    if not address:
        address = request.headers.get("x-real-ip", "").strip()
    if not address:
        address = request.client.host if request.client else "unknown"
    # The ingress appends `:port` to IPv4 entries; IPv6 keeps its own colons.
    if address.count(":") == 1:
        address = address.rsplit(":", 1)[0]
    salt = os.getenv("REACTION_VOTER_SALT", "media-app-reactions")
    return hashlib.sha256(f"{salt}:{address}".encode()).hexdigest()[:32]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/videos")
def videos(response: Response) -> list[dict]:
    response.headers["Cache-Control"] = f"public, max-age={CATALOG_CACHE_SECONDS}"
    return get_repository().videos()


@app.get("/api/videos/{video_date}/reactions")
def reaction_summary(video_date: str, request: Request) -> dict:
    try:
        return get_repository().reaction_summary(video_date, voter_id(request))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/videos/{video_date}/reactions")
def react(
    video_date: str, reaction: Reaction, request: Request, response: Response
) -> dict:
    if reaction.kind == "like":
        reaction = reaction.model_copy(update={"value": 1})
    voter = voter_id(request)
    store = get_repository()
    try:
        accepted = store.record_reaction(video_date, reaction, voter)
        summary = store.reaction_summary(video_date, voter)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    # A repeat vote still answers with the live totals so the client can render
    # them; only the status code marks the attempt as rejected.
    response.status_code = 201 if accepted else 409
    return {**summary, "accepted": accepted}


static_dir = Path(os.getenv("STATIC_DIR", "/app/static"))
if static_dir.exists():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
