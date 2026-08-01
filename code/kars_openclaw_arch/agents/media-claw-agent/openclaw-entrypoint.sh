#!/bin/sh
set -eu

seed_workspace() {
    workspace_dir=/sandbox/.openclaw/workspace
    mkdir -p "$workspace_dir"

    for name in AGENTS.md SOUL.md IDENTITY.md TOOLS.md HEARTBEAT.md; do
        cp "/opt/media-claw-workspace/$name" "$workspace_dir/$name.tmp"
        mv "$workspace_dir/$name.tmp" "$workspace_dir/$name"
        chown sandbox:sandbox "$workspace_dir/$name"
        chmod 0644 "$workspace_dir/$name"
    done
}

run_openclaw_agent() {
    proxy_url="${KARS_EGRESS_PROXY_URL:-http://127.0.0.1:8444}"
    no_proxy="${NO_PROXY:-127.0.0.1,localhost,.svc,.cluster.local,169.254.169.254}"
    if [ "$(id -u)" -eq 0 ]; then
        runuser -u sandbox -- env HOME=/sandbox \
            HTTPS_PROXY="$proxy_url" https_proxy="$proxy_url" \
            NO_PROXY="$no_proxy" no_proxy="$no_proxy" "$@"
    else
        env HOME=/sandbox \
            HTTPS_PROXY="$proxy_url" https_proxy="$proxy_url" \
            NO_PROXY="$no_proxy" no_proxy="$no_proxy" "$@"
    fi
}

run_daily_scheduler() {
    state_dir=/sandbox/.openclaw/media-scheduler
    retry_seconds="${MEDIA_SCHEDULER_RETRY_SECONDS:-900}"

    mkdir -p "$state_dir"
    chown sandbox:sandbox "$state_dir"

    while true; do
        schedule="$(TZ=Asia/Shanghai python3 -c '
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

now = datetime.now(ZoneInfo("Asia/Shanghai"))
scheduled = datetime.combine(now.date(), time(8), now.tzinfo)
if now < scheduled:
    print(f"wait {max(1, int((scheduled - now).total_seconds()))}")
else:
    print(f"run {now:%Y-%m-%d}")
')"
        action="${schedule%% *}"
        value="${schedule#* }"

        if [ "$action" = wait ]; then
            sleep "$value"
            continue
        fi

        success_marker="$state_dir/$value.success"
        if [ -f "$success_marker" ]; then
            sleep 3600
            continue
        fi

        echo "media scheduler: starting $value daily run" >&2
        if run_openclaw_agent python3 -u -m media_claw_agent.main --date "$value"; then
            touch "$success_marker"
            chown sandbox:sandbox "$success_marker"
            echo "media scheduler: completed $value daily run" >&2
        else
            echo "media scheduler: $value daily run failed; retrying in ${retry_seconds}s" >&2
            sleep "$retry_seconds"
        fi
    done
}

seed_workspace
run_daily_scheduler &
exec /usr/local/bin/kars-openclaw-entrypoint.sh "$@"