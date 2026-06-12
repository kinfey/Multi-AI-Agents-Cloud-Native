"""Configuration: Foundry models, blob storage, eval defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional for minimal runtimes
    def load_dotenv(*_args, **_kwargs):
        return False


def _load_env_files() -> None:
    """Load repo-level and service-level .env files for local dev.

    Priority (highest last):
    1) Existing process environment
    2) azure_skill_eval/.env
    3) azure_skill_eval/webapp/.env
    """
    root = Path(__file__).resolve().parent.parent
    candidates = [root / ".env", root / "webapp" / ".env"]
    for env_file in candidates:
        if env_file.exists():
            load_dotenv(env_file, override=False)


_load_env_files()


@dataclass(frozen=True)
class ModelSpec:
    label: str
    model_id: str
    foundry_agent_name: str
    foundry_agent_version: str | None = None


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_any(*names: str, default: str = "") -> str:
    for name in names:
        value = _env(name)
        if value:
            return value
    return default


def _enabled(value: str) -> bool:
    return value not in ("0", "false", "False", "")


def get_models() -> list[ModelSpec]:
    gpt_agent = _env_any(
        "FOUNDRY_AGENT_NAME_GPT",
        "AGENT_SKILL_EVAL_BUSINESS_AGENT_GPT_NAME",
        default="skill-eval-business-agent-gpt",
    )
    deepseek_agent = _env_any(
        "FOUNDRY_AGENT_NAME_DEEPSEEK",
        "AGENT_SKILL_EVAL_BUSINESS_AGENT_DEEPSEEK_NAME",
        default="skill-eval-business-agent-deepseek",
    )
    if not gpt_agent or not deepseek_agent:
        raise RuntimeError(
            "Foundry Hosted Agent names are required for the business SUT. "
            "Set FOUNDRY_AGENT_NAME_GPT and FOUNDRY_AGENT_NAME_DEEPSEEK."
        )
    return [
        ModelSpec(
            label="GPT-5.5",
            model_id=_env("MODEL_GPT", "gpt-5.5"),
            foundry_agent_name=gpt_agent,
            foundry_agent_version=_env_any(
                "FOUNDRY_AGENT_VERSION_GPT",
                "AGENT_SKILL_EVAL_BUSINESS_AGENT_GPT_VERSION",
            ) or None,
        ),
        ModelSpec(
            label="DeepSeek-V4-Pro",
            model_id=_env("MODEL_DEEPSEEK", "DeepSeek-V4-Pro"),
            foundry_agent_name=deepseek_agent,
            foundry_agent_version=_env_any(
                "FOUNDRY_AGENT_VERSION_DEEPSEEK",
                "AGENT_SKILL_EVAL_BUSINESS_AGENT_DEEPSEEK_VERSION",
            ) or None,
        ),
    ]


def judge_model() -> str:
    return _env("MODEL_JUDGE", _env("MODEL_GPT", "gpt-5.5"))


def attacker_model() -> str:
    return _env("MODEL_ATTACKER", _env("MODEL_GPT", "gpt-5.5"))


def judge_agent_name() -> str:
    value = _env_any(
        "FOUNDRY_AGENT_NAME_JUDGE",
        "AGENT_SKILL_EVAL_JUDGE_AGENT_NAME",
        default="skill-eval-judge-agent",
    )
    if not value:
        raise RuntimeError("FOUNDRY_AGENT_NAME_JUDGE is required.")
    return value


def judge_agent_version() -> str | None:
    return _env_any("FOUNDRY_AGENT_VERSION_JUDGE", "AGENT_SKILL_EVAL_JUDGE_AGENT_VERSION") or None


def attacker_agent_name() -> str:
    value = _env_any(
        "FOUNDRY_AGENT_NAME_ATTACKER",
        "AGENT_SKILL_EVAL_ATTACKER_AGENT_NAME",
        default="skill-eval-attacker-agent",
    )
    if not value:
        raise RuntimeError("FOUNDRY_AGENT_NAME_ATTACKER is required.")
    return value


def attacker_agent_version() -> str | None:
    return _env_any("FOUNDRY_AGENT_VERSION_ATTACKER", "AGENT_SKILL_EVAL_ATTACKER_AGENT_VERSION") or None


def foundry_project_endpoint() -> str:
    val = _env("FOUNDRY_PROJECT_ENDPOINT")
    if not val:
        raise RuntimeError("FOUNDRY_PROJECT_ENDPOINT is required.")
    return val


def storage_account_url() -> str:
    val = _env("AZURE_STORAGE_ACCOUNT_URL")
    if not val:
        raise RuntimeError("AZURE_STORAGE_ACCOUNT_URL is required.")
    return val


def storage_container_name() -> str:
    return _env("AZURE_STORAGE_CONTAINER", "skill-eval-runs")


def request_timeout() -> float:
    try:
        return float(_env("EVAL_REQUEST_TIMEOUT", "180"))
    except ValueError:
        return 180.0


def judge_timeout() -> float:
    try:
        return float(_env("EVAL_JUDGE_TIMEOUT", _env("EVAL_REQUEST_TIMEOUT", "180")))
    except ValueError:
        return request_timeout()


def max_turns() -> int:
    try:
        return max(1, int(_env_any("EVAL_MAX_TURNS", "MAX_TURNS", default="3")))
    except ValueError:
        return 3


def use_attack() -> bool:
    return _enabled(_env_any("EVAL_USE_ATTACK", "USE_ATTACK", default="1"))


def use_judge() -> bool:
    return _enabled(_env_any("EVAL_USE_JUDGE", "USE_JUDGE", default="1"))


def require_foundry_agent() -> bool:
    return True


def safe_label(label: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in label)
