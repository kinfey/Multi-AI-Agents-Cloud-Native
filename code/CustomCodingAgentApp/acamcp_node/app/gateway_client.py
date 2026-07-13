"""Async client for the OpenClaw gateway's OpenAI-compatible HTTP API.

The gateway exposes ``POST /v1/chat/completions`` on the same port as the
Control UI (see docs/gateway/openai-http-api.md). The OpenAI ``model`` field is
treated as an *agent target*: ``openclaw/<agentId>`` routes the turn to a
specific configured agent. Auth uses the shared gateway token as a bearer
credential.

Each turn also reports token usage (``prompt``/``completion``/``total``) so the
workflow can surface per-agent and cumulative token consumption.
"""

import os
from dataclasses import dataclass, field
from typing import Any

import httpx


class GatewayClientError(RuntimeError):
    """Raised when the gateway cannot be reached or returns an error."""


@dataclass(frozen=True)
class TokenUsage:
    """Token consumption for a single agent turn."""

    prompt: int = 0
    completion: int = 0
    total: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"prompt": self.prompt, "completion": self.completion, "total": self.total}


@dataclass(frozen=True)
class AgentTurn:
    """Result of running one agent turn: its text reply plus token usage."""

    content: str
    usage: TokenUsage = field(default_factory=TokenUsage)


def _extract_message_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, list):
        # Content parts: join any text segments.
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text", "output_text")
        ]
        return "".join(parts).strip()
    return (content or "").strip()


def _extract_usage(data: dict[str, Any]) -> TokenUsage:
    """Read the OpenAI-style ``usage`` block; default to zeros when absent."""
    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        return TokenUsage()

    def _as_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    return TokenUsage(
        prompt=_as_int(usage.get("prompt_tokens")),
        completion=_as_int(usage.get("completion_tokens")),
        total=_as_int(usage.get("total_tokens")),
    )


class OpenClawGatewayClient:
    """Client for driving OpenClaw agents via the gateway chat completions API."""

    def __init__(self, base_url: str, token: str, timeout_seconds: int) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = httpx.Timeout(timeout_seconds)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def run_agent_turn(
        self, agent_id: str, prompt: str, session_key: str | None = None
    ) -> AgentTurn:
        """Send ``prompt`` to ``agent_id`` and return its reply and token usage."""
        url = f"{self._base_url}/v1/chat/completions"
        payload: dict[str, Any] = {
            "model": f"openclaw/{agent_id}",
            "messages": [{"role": "user", "content": prompt}],
        }
        if session_key:
            # A stable `user` value lets repeated calls share one agent session.
            payload["user"] = session_key
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, headers=self._headers(), json=payload)
                response.raise_for_status()
                data = response.json()
                return AgentTurn(
                    content=_extract_message_content(data),
                    usage=_extract_usage(data),
                )
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip() or exc.response.reason_phrase
            raise GatewayClientError(
                f"Gateway returned {exc.response.status_code} for agent '{agent_id}': {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise GatewayClientError(
                f"Failed to reach gateway at {url}: {exc}"
            ) from exc

    async def run_agent(self, agent_id: str, prompt: str, session_key: str | None = None) -> str:
        """Send ``prompt`` to ``agent_id`` and return the agent's text reply."""
        turn = await self.run_agent_turn(agent_id, prompt, session_key=session_key)
        return turn.content

    async def download_artifact(self, url: str, dest_dir: str) -> str:
        """Download an authenticated artifact URL into ``dest_dir``.

        Accepts either an absolute gateway URL or a bare ``/api/...`` path.
        Returns the absolute path of the saved file.
        """
        if url.startswith("/"):
            url = f"{self._base_url}{url}"
        dest_dir = os.path.abspath(os.path.expanduser(dest_dir))
        os.makedirs(dest_dir, exist_ok=True)
        filename = os.path.basename(url.split("?", 1)[0]) or "artifact.zip"
        dest_path = os.path.join(dest_dir, filename)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, headers=self._headers())
                response.raise_for_status()
                with open(dest_path, "wb") as handle:
                    handle.write(response.content)
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip() or exc.response.reason_phrase
            raise GatewayClientError(
                f"Gateway returned {exc.response.status_code} downloading artifact: {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise GatewayClientError(
                f"Failed to download artifact from {url}: {exc}"
            ) from exc
        return dest_path

    async def health(self) -> dict[str, Any]:
        """Return the gateway health status (GET /health)."""
        url = f"{self._base_url}/health"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            raise GatewayClientError(
                f"Failed to reach gateway health endpoint at {url}: {exc}"
            ) from exc
