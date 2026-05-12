"""FastAPI entry point for the Kata-microVM-isolated Copilot SDK agent."""

from __future__ import annotations

import contextlib
import logging
import os
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .agent import build_agent

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("copilot-agent")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    stream: bool = False


class ChatResponse(BaseModel):
    reply: str


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    agent = build_agent()
    await agent.start()
    app.state.agent = agent
    logger.info("agent started: name=%s id=%s", agent.name, agent.id)
    try:
        yield
    finally:
        await agent.stop()
        logger.info("agent stopped")


app = FastAPI(
    title="Kata microVM Copilot Agent",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    agent = getattr(app.state, "agent", None)
    if agent is None:
        raise HTTPException(status_code=503, detail="agent not initialised")
    return {"status": "ready", "agent": agent.name or "copilot"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    agent = app.state.agent
    if req.stream:
        raise HTTPException(status_code=400, detail="Use POST /chat/stream for streaming")
    try:
        result = await agent.run(req.message)
    except Exception as exc:  # surface agent errors as 502
        logger.exception("agent.run failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ChatResponse(reply=str(result))


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    agent = app.state.agent

    async def gen() -> AsyncIterator[bytes]:
        try:
            async for chunk in agent.run(req.message, stream=True):
                if getattr(chunk, "text", None):
                    yield chunk.text.encode("utf-8")
        except Exception as exc:
            logger.exception("agent.run streaming failed")
            yield f"\n[error] {exc}\n".encode("utf-8")

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")
