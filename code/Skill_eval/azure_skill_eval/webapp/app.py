"""FastAPI webapp — upload evals.json, run evaluation, serve dashboard.

Deployed to Azure Container Apps. Authenticates to Foundry & Blob Storage
using :class:`DefaultAzureCredential` (managed identity in production,
``az login`` locally).
"""
from __future__ import annotations

import asyncio
import datetime
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from azure.identity.aio import DefaultAzureCredential
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

# Make the sibling ``shared/`` package importable when run from this dir.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.blob_store import BlobStore, new_run_id  # noqa: E402
from shared.runner import RunOptions, options_from_env, run_evaluation  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
TEMPLATE_DIR = APP_DIR / "templates"

app = FastAPI(title="Skill Eval (Foundry / Container Apps)")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# In-process job registry
# ---------------------------------------------------------------------------
class JobState:
    def __init__(self, run_id: str, total: int = 0) -> None:
        self.run_id = run_id
        self.status: str = "queued"  # queued | running | completed | failed
        self.done: int = 0
        self.total: int = total
        self.error: str | None = None
        self.started_at: str = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
        self.finished_at: str | None = None
        self.last_event: dict[str, Any] = {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "done": self.done,
            "total": self.total,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "last_event": self.last_event,
        }


JOBS: dict[str, JobState] = {}


async def _job_runner(payload: dict[str, Any] | None, options: RunOptions, state: JobState) -> None:
    async def progress(event: dict[str, Any]) -> None:
        state.last_event = event
        etype = event.get("type")
        if etype == "run_started":
            state.status = "running"
            state.total = int(event.get("total") or state.total)
        elif etype == "case_finished":
            state.done = int(event.get("done") or state.done)
        elif etype == "run_finished":
            state.status = "completed"
            state.done = state.total
            state.finished_at = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()

    try:
        import sys as _sys
        print(f"[RUNNER] Starting job {state.run_id}", file=_sys.stderr, flush=True)
        await run_evaluation(
            evals_payload=payload,
            options=options,
            progress=progress,
            run_id=state.run_id,
        )
        print(f"[RUNNER] Completed job {state.run_id} with status {state.status}", file=_sys.stderr, flush=True)
    except Exception as e:  # noqa: BLE001
        import sys as _sys
        state.status = "failed"
        state.error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}"
        state.finished_at = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
        print(f"[RUNNER] Error in job {state.run_id}: {state.error}", file=_sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((TEMPLATE_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "dashboard.html").read_text(encoding="utf-8"))


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------
@app.post("/api/run")
async def api_run(
    file: UploadFile | None = File(None),
    use_attack: int | None = None,
    use_judge: int | None = None,
    single_turn: int | None = None,
    max_turns: int | None = None,
    only_case: str | None = None,
    model: str | None = None,
) -> JSONResponse:
    """Start an evaluation job.

    ``file`` is an optional ``evals.json`` upload. If omitted, the default
    10-case set baked into ``shared.test_cases`` is used.
    """
    payload: dict[str, Any] | None = None
    if file is not None:
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Empty file upload")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e

    opts = options_from_env()
    if use_attack is not None:
        opts.use_attack = bool(use_attack)
    if use_judge is not None:
        opts.use_judge = bool(use_judge)
    if single_turn is not None:
        opts.single_turn = bool(single_turn)
    if max_turns is not None:
        opts.max_turns = max(1, int(max_turns))
    if only_case:
        opts.only_case = only_case.strip()
    if model:
        opts.model = model.strip()

    run_id = new_run_id()
    state = JobState(run_id=run_id)
    JOBS[run_id] = state
    asyncio.create_task(_job_runner(payload, opts, state))
    return JSONResponse({"run_id": run_id, "status": state.status})


@app.get("/api/debug/jobs")
async def debug_jobs() -> JSONResponse:
    """Diagnostic endpoint: show in-memory job registry."""
    return JSONResponse({
        "count": len(JOBS),
        "jobs": {rid: state.to_dict() for rid, state in JOBS.items()},
    })


@app.get("/api/jobs/{run_id}")
async def api_job(run_id: str) -> JSONResponse:
    state = JOBS.get(run_id)
    if state is None:
        # Job not in memory (may have been completed in a previous process) —
        # check Blob for the per-run index instead.
        try:
            async with BlobStore() as store:
                idx = await store.download_json(f"{run_id}/index.json")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"Blob access failed: {e}") from e
        if idx is None:
            return JSONResponse({
                "run_id": run_id,
                "status": "failed",
                "done": 0,
                "total": 0,
                "error": "run not found (likely stale run id after server restart)",
            })
        return JSONResponse({
            "run_id": run_id,
            "status": "completed",
            "done": idx.get("total_count"),
            "total": idx.get("total_count"),
            "index": idx,
        })
    return JSONResponse(state.to_dict())


@app.get("/api/runs")
async def api_runs() -> JSONResponse:
    try:
        async with BlobStore() as store:
            runs = await store.list_runs()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Blob access failed: {e}") from e
    return JSONResponse(runs)


@app.get("/api/runs/{run_id}")
async def api_run_index(run_id: str) -> JSONResponse:
    try:
        async with BlobStore() as store:
            idx = await store.download_json(f"{run_id}/index.json")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Blob access failed: {e}") from e
    if idx is None:
        raise HTTPException(status_code=404, detail="run not found")
    return JSONResponse(idx)


@app.get("/api/runs/{run_id}/files/{filename}")
async def api_run_file(run_id: str, filename: str) -> Response:
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="invalid filename")
    try:
        async with BlobStore() as store:
            data = await store.download_json(f"{run_id}/{filename}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Blob access failed: {e}") from e
    if data is None:
        raise HTTPException(status_code=404, detail="file not found")
    return JSONResponse(data)


@app.get("/api/runs/{run_id}/summary")
async def api_run_summary(run_id: str) -> JSONResponse:
    try:
        async with BlobStore() as store:
            data = await store.download_json(f"{run_id}/summary.json")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Blob access failed: {e}") from e
    if data is None:
        raise HTTPException(status_code=404, detail="summary not found")
    return JSONResponse(data)


# ---------------------------------------------------------------------------
# Local dev entry-point: ``python -m webapp.app`` or ``python webapp/app.py``
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
