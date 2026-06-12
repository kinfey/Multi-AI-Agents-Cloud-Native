"""Cross-version helpers for invoking ``Agent`` / Foundry hosted-agent objects."""
from __future__ import annotations

import uuid
import gzip
import json
import zlib
from typing import Any

from azure.core.exceptions import StreamConsumedError
from azure.core.rest import HttpRequest


def _text_from_response(response: Any) -> str:
    if response is None:
        return ""
    for attr in ("text", "output_text", "content"):
        val = getattr(response, attr, None)
        if isinstance(val, str) and val:
            return val
    messages = getattr(response, "messages", None)
    if messages:
        last = messages[-1]
        for attr in ("text", "content"):
            val = getattr(last, attr, None)
            if isinstance(val, str) and val:
                return val
            if isinstance(val, list) and val:
                pieces = []
                for chunk in val:
                    chunk_text = getattr(chunk, "text", None)
                    if isinstance(chunk_text, str):
                        pieces.append(chunk_text)
                    elif isinstance(chunk, str):
                        pieces.append(chunk)
                if pieces:
                    return "".join(pieces)
    return str(response)


async def run_agent_text(agent: Any, prompt: str) -> str:
    """Invoke ``agent.run(prompt)`` and return assistant text in a way that
    works across agent-framework / FoundryAgent shapes."""
    if getattr(agent, "_skill_eval_is_foundry_hosted_agent", False):
        return await run_foundry_hosted_agent_text(agent, prompt)
    response = await agent.run(prompt)
    return _text_from_response(response)


async def run_foundry_hosted_agent_text(agent: Any, prompt: str) -> str:
    """Run a Foundry Hosted Agent using the official responses sample pattern.

    The deployed Foundry agent requires a service session created through
    ``AIProjectClient.beta.agents.create_session``. A fresh isolation key is
    used per call so each eval turn is independent.
    """
    project_client = getattr(agent, "_skill_eval_project_client")
    agent_name = getattr(agent, "_skill_eval_agent_name")
    agent_version = getattr(agent, "_skill_eval_agent_version", None)
    session_id = f"skill-eval-{uuid.uuid4().hex}"

    from azure.ai.projects.models import VersionRefIndicator

    service_session = await _create_hosted_session_rest(
        project_client=project_client,
        agent_name=agent_name,
        session_id=session_id,
        version_indicator=VersionRefIndicator(agent_version=agent_version or "@latest"),
    )
    service_session_id = service_session.get("agent_session_id") or service_session.get("id")
    if not isinstance(service_session_id, str) or not service_session_id:
        raise ValueError("Hosted agent session creation did not return a session id.")

    session = agent.get_session(service_session_id)
    try:
        response = await agent.run(prompt, session=session)
        return _text_from_response(response)
    finally:
        await _delete_hosted_session_rest(project_client, agent_name, service_session_id)


def _decode_response_body(data: bytes, encoding: str | None = None) -> str:
    errors: list[Exception] = []
    if encoding and "br" in encoding.lower():
        try:
            import brotli
            return brotli.decompress(data).decode("utf-8-sig")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    for decode in (
        lambda b: b.decode("utf-8-sig"),
        lambda b: gzip.decompress(b).decode("utf-8-sig"),
        lambda b: zlib.decompress(b).decode("utf-8-sig"),
        lambda b: zlib.decompress(b, -zlib.MAX_WBITS).decode("utf-8-sig"),
    ):
        try:
            return decode(data)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
    try:
        import brotli
        return brotli.decompress(data).decode("utf-8-sig")
    except Exception as exc:  # noqa: BLE001
        errors.append(exc)
    raise UnicodeDecodeError("utf-8", data, 0, min(len(data), 1), f"unable to decode response body: {errors[-1]}")


async def _send_project_json(project_client: Any, request: HttpRequest) -> tuple[int, dict[str, Any]]:
    pipeline_response = await project_client._client._pipeline.run(request)  # pylint: disable=protected-access
    response = pipeline_response.http_response
    try:
        data = await response.read()
    except StreamConsumedError:
        return response.status_code, {}
    if not data:
        return response.status_code, {}
    return response.status_code, json.loads(_decode_response_body(data, response.headers.get("Content-Encoding")))


def _project_url(project_client: Any, path: str) -> str:
    base = project_client._config.endpoint.rstrip("/")  # pylint: disable=protected-access
    return f"{base}{path}"


async def _create_hosted_session_rest(
    project_client: Any,
    agent_name: str,
    session_id: str,
    version_indicator: Any,
) -> dict[str, Any]:
    body = json.dumps({
        "agent_session_id": session_id,
        "version_indicator": version_indicator.as_dict(),
    })
    request = HttpRequest(
        "POST",
        _project_url(project_client, f"/agents/{agent_name}/endpoint/sessions?api-version=v1"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Foundry-Features": "HostedAgents=V1Preview",
        },
        content=body,
    )
    status_code, payload = await _send_project_json(project_client, request)
    if status_code != 201:
        raise RuntimeError(f"Failed to create hosted-agent session ({status_code}): {payload}")
    return payload


async def _delete_hosted_session_rest(project_client: Any, agent_name: str, session_id: str) -> None:
    request = HttpRequest(
        "DELETE",
        _project_url(project_client, f"/agents/{agent_name}/endpoint/sessions/{session_id}?api-version=v1"),
        headers={"Accept": "application/json", "Foundry-Features": "HostedAgents=V1Preview"},
    )
    status_code, payload = await _send_project_json(project_client, request)
    if status_code not in (200, 202, 204, 404):
        raise RuntimeError(f"Failed to delete hosted-agent session ({status_code}): {payload}")
