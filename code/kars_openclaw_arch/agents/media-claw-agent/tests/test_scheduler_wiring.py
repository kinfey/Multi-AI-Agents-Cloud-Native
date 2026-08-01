from pathlib import Path

import yaml

ROOT = Path(__file__).parents[3]


def test_entrypoint_runs_persistent_daily_local_scheduler() -> None:
    entrypoint = (ROOT / "agents/media-claw-agent/openclaw-entrypoint.sh").read_text()

    assert "ZoneInfo(\"Asia/Shanghai\")" in entrypoint
    assert "time(8)" in entrypoint
    assert 'success_marker="$state_dir/$value.success"' in entrypoint
    assert 'MEDIA_SCHEDULER_RETRY_SECONDS:-900' in entrypoint
    assert 'python3 -u -m media_claw_agent.main --date "$value"' in entrypoint
    assert "openclaw agent" not in entrypoint
    assert 'touch "$success_marker"' in entrypoint
    assert 'if [ "$(id -u)" -eq 0 ]' in entrypoint
    assert "runuser -u sandbox -- env HOME=/sandbox" in entrypoint
    assert 'KARS_EGRESS_PROXY_URL:-http://127.0.0.1:8444' in entrypoint
    assert 'HTTPS_PROXY="$proxy_url"' in entrypoint
    assert ".svc,.cluster.local,169.254.169.254" in entrypoint
    assert entrypoint.count("env HOME=/sandbox") == 2
    assert "cron add" not in entrypoint
    assert 'mkdir -p "$workspace_dir"' in entrypoint
    assert 'mv "$workspace_dir/$name.tmp" "$workspace_dir/$name"' in entrypoint
    assert 'chown sandbox:sandbox "$workspace_dir/$name"' in entrypoint
    assert 'chmod 0644 "$workspace_dir/$name"' in entrypoint
    assert 'rm -f "$workspace_dir/AGENTS.md"' not in entrypoint
    assert 'while [ ! -f "$workspace_dir/SOUL.md" ]' not in entrypoint
    assert "seed_workspace\nrun_daily_scheduler &" in entrypoint
    assert "while [ ! -f /sandbox/.openclaw/workspace/HEARTBEAT.md ]" not in entrypoint


def test_compose_runs_media_claw_with_persistent_scheduler_state() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    service = compose["services"]["media-claw-agent"]

    assert service["build"]["dockerfile"] == "agents/media-claw-agent/Dockerfile"
    assert "NET_ADMIN" in service["cap_add"]
    assert service["environment"]["AGT_SKIP_INIT"] == "1"
    assert "media-claw-state:/sandbox" in service["volumes"]
    assert compose["volumes"]["media-claw-state"] is None