import os
from dataclasses import dataclass


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_local_save_dir() -> str:
    """Default local destination for saved prototype artifacts.

    When the save-agent produces a download URL but no explicit destination is
    given, the workflow downloads the archive here. Defaults to the user's
    Downloads folder so prototypes land somewhere obvious on the local machine.
    """
    raw = os.environ.get("OPENCLAW_LOCAL_SAVE_DIR", "").strip()
    if raw:
        return os.path.abspath(os.path.expanduser(raw))
    return os.path.join(os.path.expanduser("~"), "Downloads")


# Ordered agents of the OpenClaw programming workflow. Each maps to a gateway
# agent target (model = "openclaw/<agent_id>").
DEFAULT_AGENTS: tuple[str, ...] = (
    "requirements-agent",
    "coding-agent",
    "testing-agent",
    "deployment-agent",
    "save-agent",
)


@dataclass(frozen=True)
class Settings:
    """Runtime configuration for the MCP service, sourced from environment vars."""

    gateway_base_url: str
    gateway_token: str
    request_timeout_seconds: int
    agents: tuple[str, ...]
    host: str
    port: int
    local_save_dir: str

    @classmethod
    def from_env(cls) -> "Settings":
        base_url = os.environ.get(
            "OPENCLAW_GATEWAY_URL",
            "https://azure-openclaw-aca-app.bluedune-876fc257.swedencentral.azurecontainerapps.io",
        ).rstrip("/")
        return cls(
            gateway_base_url=base_url,
            gateway_token=os.environ.get("OPENCLAW_GATEWAY_TOKEN", ""),
            request_timeout_seconds=_get_int("GATEWAY_TIMEOUT_SECONDS", 300),
            agents=DEFAULT_AGENTS,
            host=os.environ.get("MCP_HOST", "0.0.0.0"),
            port=_get_int("MCP_PORT", 8000),
            local_save_dir=_get_local_save_dir(),
        )

