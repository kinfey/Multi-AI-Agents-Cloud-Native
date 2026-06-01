"""Builds an Agent Framework chat client pointed at the AI Runway OpenAI-compatible API.

The KAITO + llama.cpp deployment behind AI Runway exposes the standard
/v1/chat/completions surface, so `agent_framework.openai.OpenAIChatCompletionClient`
works as-is once we override `base_url`.
"""

from __future__ import annotations

import os

from agent_framework.openai import OpenAIChatCompletionClient


def build_chat_client() -> OpenAIChatCompletionClient:
    """Return a chat client wired to the AI Runway Service in-cluster."""
    base_url = os.environ.get(
        "AIRUNWAY_BASE_URL",
        "http://llama3-2-1b-cpu.airunway-models.svc.cluster.local:8000/v1",
    )
    model = os.environ.get("AIRUNWAY_MODEL", "Qwen/Qwen3-0.6B")
    # AI Runway/KAITO's OpenAI server does not require a real key, but the
    # OpenAI SDK insists on a non-empty string. "sk-airunway" is a placeholder.
    api_key = os.environ.get("AIRUNWAY_API_KEY", "sk-airunway")

    return OpenAIChatCompletionClient(
        model=model,
        api_key=api_key,
        base_url=base_url,
    )
