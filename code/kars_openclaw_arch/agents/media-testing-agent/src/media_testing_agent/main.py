from __future__ import annotations

import os
from pathlib import Path

from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from .models import EvalReport


class ReportRepository:
    def __init__(self) -> None:
        client_id = os.getenv("AZURE_CLIENT_ID")
        credential = (
            ManagedIdentityCredential(client_id=client_id)
            if client_id
            else DefaultAzureCredential(exclude_interactive_browser_credential=True)
        )
        service = BlobServiceClient(os.environ["AZURE_STORAGE_ACCOUNT_URL"], credential)
        self._container = service.get_container_client(
            os.getenv("AZURE_REPORT_CONTAINER", "redteam")
        )

    def list_reports(self) -> list[EvalReport]:
        reports = [
            EvalReport.model_validate_json(self._container.download_blob(blob.name).readall())
            for blob in self._container.list_blobs(name_starts_with="reports/")
            if blob.name.endswith(".json")
        ]
        return sorted(reports, key=lambda report: report.created_at, reverse=True)


app = FastAPI(title="Media Testing Agent", version="1.0.0")
repository: ReportRepository | None = None


@app.middleware("http")
async def revalidate_static_assets(request: Request, call_next):
    # Static HTML/JS/CSS must always be revalidated so a redeploy is picked up
    # immediately instead of being served from a stale browser cache. StaticFiles
    # emits an ETag, so revalidation returns a cheap 304 when nothing changed.
    response = await call_next(request)
    if not request.url.path.startswith(("/api/", "/health")):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


def get_repository() -> ReportRepository:
    global repository
    if repository is None:
        repository = ReportRepository()
    return repository


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/reports")
def reports() -> list[dict]:
    return [report.public_dict() for report in get_repository().list_reports()]


static_dir = Path(os.getenv("STATIC_DIR", "/app/static"))
if static_dir.exists():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
