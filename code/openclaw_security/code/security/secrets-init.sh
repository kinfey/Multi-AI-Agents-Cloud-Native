#!/usr/bin/env sh
# secrets-init.sh — Token rotation + audit monitoring on startup
#
# 1. Regenerates gateway-token on each container startup, writes to tmpfs /run/secrets/
# 2. inotifywait monitors /run/secrets for all access events (audit log)

set -e

SECRETS_DIR="/run/secrets"
TOKEN_FILE="$SECRETS_DIR/gateway-token"
AUDIT_LOG="$SECRETS_DIR/audit.log"

mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"

# ── 1. Regenerate gateway token on each startup ────────────────────────────
echo "[secrets-init] Generating new gateway token..."

NEW_TOKEN=$(cat /proc/sys/kernel/random/uuid 2>/dev/null | tr -d '-' || \
            head -c 32 /dev/urandom | xxd -p | head -c 48)

# ── 1a. Sync token to openclaw.json FIRST (before writing gateway-token) ────
# Must happen before the healthcheck file is written so openclaw cannot start
# with the old config token and then receive requests signed with the new token.
OPENCLAW_CONFIG="/openclaw-config/openclaw.json"
if command -v jq >/dev/null 2>&1 && [ -f "$OPENCLAW_CONFIG" ]; then
  jq --arg tok "$NEW_TOKEN" '.gateway.auth.token = $tok' "$OPENCLAW_CONFIG" > /tmp/oc.tmp
  mv /tmp/oc.tmp "$OPENCLAW_CONFIG"
  echo "[secrets-init] Token synced to openclaw.json"
else
  echo "[secrets-init] WARNING: jq or $OPENCLAW_CONFIG not found, skipping openclaw.json sync"
fi

# ── 1b. Write gateway-token (healthcheck target — only reached after jq sync) ─
echo "$NEW_TOKEN" > "$TOKEN_FILE"
chmod 400 "$TOKEN_FILE"

echo "[secrets-init] Token written to $TOKEN_FILE ($(wc -c < $TOKEN_FILE) bytes)"

# openclaw.json auth.token is read from this file
echo "OPENCLAW_GATEWAY_TOKEN=$NEW_TOKEN" > "$SECRETS_DIR/gateway.env"
chmod 400 "$SECRETS_DIR/gateway.env"

# ── 2. inotifywait monitoring of secrets directory ──────────────────────────
if command -v inotifywait >/dev/null 2>&1; then
  echo "[secrets-init] Starting secrets directory audit monitoring..."
  (
    echo "$(date -Iseconds) [audit] secrets directory monitoring started" >> "$AUDIT_LOG"
    inotifywait -m -r --format '%T %e %w%f' --timefmt '%Y-%m-%dT%H:%M:%S' \
      -e access -e modify -e open -e create -e delete -e attrib \
      "$SECRETS_DIR" 2>/dev/null | while read -r line; do
        echo "[secrets-audit] $line" | tee -a "$AUDIT_LOG"
    done
  ) &
  echo "[secrets-init] Audit monitor PID: $!"
else
  echo "[secrets-init] inotifywait not available, skipping audit monitoring"
fi

echo "[secrets-init] Secrets initialization complete"
echo "[secrets-init] TOKEN first 8 chars: ${NEW_TOKEN%${NEW_TOKEN#????????}}..."
