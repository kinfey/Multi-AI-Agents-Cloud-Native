"""Model configuration for the testing harness.

Two backends are exercised through the GitHub Copilot CLI: Claude Opus 4.7
and GPT-5.5. The exact model IDs accepted by `copilot --model` change over
time; override with env vars `MODEL_CLAUDE` / `MODEL_GPT` if needed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    label: str          # Human-readable label shown in the console
    model_id: str       # Value passed to GitHub Copilot CLI as --model


MODELS: list[ModelSpec] = [
    ModelSpec(
        label="Claude Opus 4.7",
        model_id=os.getenv("MODEL_CLAUDE", "claude-opus-4.7"),
    ),
    ModelSpec(
        label="GPT-5.5",
        model_id=os.getenv("MODEL_GPT", "gpt-5.5"),
    ),
]

# Per-call timeout (seconds) handed to the Copilot CLI.
REQUEST_TIMEOUT = int(os.getenv("GITHUB_COPILOT_TIMEOUT", "180"))
