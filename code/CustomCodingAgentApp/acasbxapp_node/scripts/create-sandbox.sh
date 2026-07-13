#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/load-env.sh"
load_env_file "$SCRIPT_DIR/../.env"

LABEL_NAME="${1:-${AZURE_OPENCLAW_PREFIX:-azure-openclaw-aca}}"
SANDBOX_DISK="${ACA_SANDBOX_DISK:-ubuntu}"
# Private disk image (committed via `aca sandbox commit`) that bakes in the
# azure-cli + containerapp extension + deploy helpers so the deployment-agent
# can build/deploy from inside the sandbox. Takes precedence over SANDBOX_DISK.
SANDBOX_DISK_ID="${ACA_SANDBOX_DISK_ID:-}"
ENV_FILE="$SCRIPT_DIR/../.env"

upsert_env() {
  local key="$1"
  local value="$2"
  if [ ! -f "$ENV_FILE" ]; then
    touch "$ENV_FILE"
  fi
  if grep -q "^${key}=" "$ENV_FILE"; then
    perl -0pi -e "s#^${key}=.*\$#${key}=${value}#m" "$ENV_FILE"
  else
    printf '\n%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

if [ -n "$SANDBOX_DISK_ID" ]; then
  CREATE_OUTPUT="$(aca sandbox create --disk-id "$SANDBOX_DISK_ID" --label "name=${LABEL_NAME}")"
else
  CREATE_OUTPUT="$(aca sandbox create --disk "$SANDBOX_DISK" --label "name=${LABEL_NAME}")"
fi
SANDBOX_ID="$(printf '%s\n' "$CREATE_OUTPUT" | sed -n 's/^Created sandbox: //p' | tail -n1)"
[ -n "$SANDBOX_ID" ] || {
  echo "Failed to parse sandbox id from aca sandbox create output." >&2
  exit 1
}

for _ in $(seq 1 30); do
  if aca sandbox exec --id "$SANDBOX_ID" -c "true" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

aca sandbox exec --id "$SANDBOX_ID" -c "echo 'ACA Sandbox ready for manual exec workflows.'"
upsert_env OPENCLAW_ACA_SANDBOX_ID "$SANDBOX_ID"
printf 'Sandbox ready: %s\nUpdated %s with OPENCLAW_ACA_SANDBOX_ID.\n' "$SANDBOX_ID" "$ENV_FILE"
