#!/usr/bin/env bash
# Smoke-check the deployed MCP server: verify the gateway health tool and the
# MCP initialize handshake via port-forward.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$SCRIPT_DIR/load-env.sh"
load_env_file "$MCP_ROOT/.env"

NAMESPACE="${K8S_NAMESPACE:-openclaw}"
MCP_LOCAL_PORT="${MCP_LOCAL_PORT:-18000}"

cleanup() {
  [ -n "${MCP_PF_PID:-}" ] && kill "$MCP_PF_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo "== Port-forwarding MCP service =="
kubectl -n "$NAMESPACE" port-forward svc/acamcp-server "${MCP_LOCAL_PORT}:80" >/dev/null 2>&1 &
MCP_PF_PID=$!
sleep 4

echo "== MCP initialize handshake (/mcp) =="
INIT_BODY='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"1.0"}}}'
RESPONSE="$(curl -fsS -X POST "http://127.0.0.1:${MCP_LOCAL_PORT}/mcp" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d "$INIT_BODY")"

echo "$RESPONSE"
if echo "$RESPONSE" | grep -q '"serverInfo"'; then
  echo "MCP handshake OK"
else
  echo "MCP handshake did NOT return serverInfo" >&2
  exit 1
fi
