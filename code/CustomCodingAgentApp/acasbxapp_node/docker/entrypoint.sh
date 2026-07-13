#!/usr/bin/env sh
set -eu

export OPENCLAW_PAIRING_STATE_DIR=/state/openclaw
export OPENCLAW_ARTIFACTS_ROOT="${OPENCLAW_ARTIFACTS_ROOT:-/state/openclaw/artifacts}"
export OPENCLAW_WORKSPACES_ROOT="${OPENCLAW_WORKSPACES_ROOT:-/state/openclaw/workspaces}"

mkdir -p "$OPENCLAW_WORKSPACES_ROOT" "$OPENCLAW_ARTIFACTS_ROOT" /tmp/openclaw/agents
chmod 700 "$OPENCLAW_ARTIFACTS_ROOT"
chmod 700 /tmp/openclaw /tmp/openclaw/agents

FOUNDRY_BASE_URL="${AZURE_AI_FOUNDRY_ENDPOINT%/}"
case "$FOUNDRY_BASE_URL" in
  */openai/v1)
    ;;
  */models)
    FOUNDRY_BASE_URL="${FOUNDRY_BASE_URL%/models}/openai/v1"
    ;;
  */openai/deployments/*)
    FOUNDRY_BASE_URL="${FOUNDRY_BASE_URL%%/openai/deployments/*}/openai/v1"
    ;;
  *)
    FOUNDRY_BASE_URL="${FOUNDRY_BASE_URL}/openai/v1"
    ;;
esac

CONTROL_UI_ALLOWED_ORIGINS='"http://localhost:18789","http://127.0.0.1:18789"'
GATEWAY_PUBLIC_ORIGIN_SOURCE="${OPENCLAW_GATEWAY_URL:-}"
if [ "$GATEWAY_PUBLIC_ORIGIN_SOURCE" = "" ] && [ "${CONTAINER_APP_NAME:-}" != "" ] && [ "${CONTAINER_APP_ENV_DNS_SUFFIX:-}" != "" ]; then
  GATEWAY_PUBLIC_ORIGIN_SOURCE="https://${CONTAINER_APP_NAME}.${CONTAINER_APP_ENV_DNS_SUFFIX}"
fi
if [ "$GATEWAY_PUBLIC_ORIGIN_SOURCE" != "" ]; then
  GATEWAY_PUBLIC_ORIGIN="$(printf '%s' "$GATEWAY_PUBLIC_ORIGIN_SOURCE" | sed -E 's#^((https?|wss?)://[^/]+).*$#\1#')"
  case "$GATEWAY_PUBLIC_ORIGIN" in
    ws://*)
      GATEWAY_PUBLIC_ORIGIN="http://${GATEWAY_PUBLIC_ORIGIN#ws://}"
      ;;
    wss://*)
      GATEWAY_PUBLIC_ORIGIN="https://${GATEWAY_PUBLIC_ORIGIN#wss://}"
      ;;
  esac
  case ",${CONTROL_UI_ALLOWED_ORIGINS}," in
    *"\"${GATEWAY_PUBLIC_ORIGIN}\""*)
      ;;
    *)
      CONTROL_UI_ALLOWED_ORIGINS="${CONTROL_UI_ALLOWED_ORIGINS},\"${GATEWAY_PUBLIC_ORIGIN}\""
      ;;
  esac
fi
export OPENCLAW_GATEWAY_URL="${GATEWAY_PUBLIC_ORIGIN_SOURCE%/}"

for agent in requirements-agent coding-agent testing-agent deployment-agent save-agent; do
  mkdir -p "$OPENCLAW_WORKSPACES_ROOT/${agent}" "/tmp/openclaw/agents/${agent}/agent"
  chmod 700 "/tmp/openclaw/agents/${agent}" "/tmp/openclaw/agents/${agent}/agent"
  cp "/opt/openclaw-agents/${agent}/AGENTS.md" "/tmp/openclaw/agents/${agent}/agent/AGENTS.md"
done

cat > "${OPENCLAW_CONFIG_PATH:-/state/openclaw/openclaw.json5}" <<EOF
{
  gateway: {
    mode: "remote",
    bind: "lan",
    port: 18789,
    auth: { mode: "token", token: "${OPENCLAW_GATEWAY_TOKEN}" },
    controlUi: {
      allowedOrigins: [${CONTROL_UI_ALLOWED_ORIGINS}],
    },
    http: {
      endpoints: {
        chatCompletions: { enabled: true },
      },
    },
  },
  models: {
    providers: {
      "microsoft-foundry": {
        baseUrl: "${FOUNDRY_BASE_URL}",
        apiKey: "__entra_id_dynamic__",
        api: "openai-responses",
      },
    },
  },
  agents: {
    defaults: {
      model: "microsoft-foundry/${AZURE_AI_FOUNDRY_MODEL_DEPLOYMENT}",
      workspace: "/state/openclaw/workspaces",
      sandbox: {
        mode: "all",
        backend: "aca",
        scope: "shared",
        workspaceAccess: "rw",
        aca: {
          id: "${OPENCLAW_ACA_SANDBOX_ID}",
          sandboxGroup: "${OPENCLAW_ACA_SANDBOX_GROUP}",
          resourceGroup: "${AZURE_RESOURCE_GROUP}",
          region: "${AZURE_LOCATION}",
          workspaceRoot: "/tmp/openclaw-sandboxes",
        },
      },
    },
    list: [
      { id: "requirements-agent", name: "Requirement Agent", workspace: "/state/openclaw/workspaces/project", agentDir: "/tmp/openclaw/agents/requirements-agent/agent" },
      { id: "coding-agent", name: "Coding Agent", workspace: "/state/openclaw/workspaces/project", agentDir: "/tmp/openclaw/agents/coding-agent/agent" },
      { id: "testing-agent", name: "Testing Agent", workspace: "/state/openclaw/workspaces/project", agentDir: "/tmp/openclaw/agents/testing-agent/agent" },
      { id: "deployment-agent", name: "Deployment Agent", workspace: "/state/openclaw/workspaces/project", agentDir: "/tmp/openclaw/agents/deployment-agent/agent" },
      { id: "save-agent", name: "Save Agent", workspace: "/state/openclaw/workspaces/project", agentDir: "/tmp/openclaw/agents/save-agent/agent" },
    ],
  },
  plugins: {
    entries: {
      "save-agent": { enabled: true },
    },
  },
  tools: {
    allow: ["read", "write", "edit", "apply_patch", "exec", "sessions_list", "sessions_history", "sessions_send"],
    agentToAgent: { enabled: true, allow: ["requirements-agent", "coding-agent", "testing-agent", "deployment-agent", "save-agent"] },
  },
}
EOF

if [ "${AZURE_CLIENT_ID:-}" != "" ]; then
  az login --identity --client-id "$AZURE_CLIENT_ID" >/dev/null
else
  az login --identity >/dev/null || true
fi

exec node dist/index.js gateway --bind lan --port 18789 --allow-unconfigured --token "${OPENCLAW_GATEWAY_TOKEN}"
