#!/usr/bin/env bash
# MCP smoke/e2e test over HTTPS using only curl.
#
# Performs the MCP streamable-HTTP handshake (initialize -> initialized) and
# then calls the `generate_prototype` tool with a plain-language requirement,
# streaming the multi-agent workflow output. Finally it extracts the save-agent
# DOWNLOAD_URL from the content, downloads the delivered ZIP, extracts it into a
# local `code/` directory, and opens it in VS Code Insiders (or stable VS Code).
#
# Usage:
#   scripts/mcp-curl-test.sh "<your requirement prompt>"
#
# Environment (with sensible defaults):
#   MCP_URL                 MCP endpoint (default: https://74.241.158.87.nip.io/mcp)
#   MCP_BASIC_AUTH_USER     Basic auth user (default: mcp)
#   MCP_BASIC_AUTH_PASSWORD Basic auth password (required if the ingress enforces auth)
#   OPENCLAW_GATEWAY_TOKEN  Bearer token to download the ZIP (optional; read from
#                           acasbxapp_node/.env when not set)
#   DOWNLOAD_DIR            Where to save the ZIP (default: ~/Downloads)
#   CODE_DIR                Where to extract + open the project (default: <repo>/code)
#   TOOL                    Tool to call: generate_prototype (default) | check_gateway_health
set -euo pipefail

REQUIREMENT="${1:-Create a World Cup special page (group stage + knockout stage) in the style of BBC Sport. Backend: Python FastAPI serving a REST API. Frontend: an HTML5 + CSS3 + JavaScript single-page application (no framework).}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

MCP_URL="${MCP_URL:-https://74.241.158.87.nip.io/mcp}"
MCP_BASIC_AUTH_USER="${MCP_BASIC_AUTH_USER:-mcp}"
MCP_BASIC_AUTH_PASSWORD="${MCP_BASIC_AUTH_PASSWORD:-}"
DOWNLOAD_DIR="${DOWNLOAD_DIR:-$HOME/Downloads}"
TOOL="${TOOL:-generate_prototype}"

# Read the gateway token from acasbxapp_node/.env if not provided (to download the ZIP).
if [ -z "${OPENCLAW_GATEWAY_TOKEN:-}" ] && [ -f "$REPO_ROOT/acasbxapp_node/.env" ]; then
  OPENCLAW_GATEWAY_TOKEN="$(grep -E '^OPENCLAW_GATEWAY_TOKEN=' "$REPO_ROOT/acasbxapp_node/.env" | head -1 | cut -d= -f2-)"
fi

AUTH_ARGS=()
if [ -n "$MCP_BASIC_AUTH_PASSWORD" ]; then
  AUTH_ARGS=(-u "${MCP_BASIC_AUTH_USER}:${MCP_BASIC_AUTH_PASSWORD}")
fi

ACCEPT='application/json, text/event-stream'
CT='Content-Type: application/json'

echo "== MCP endpoint: $MCP_URL"
echo "== tool: $TOOL"

# 1) initialize -> capture the session id from the response headers.
echo "== [1/3] initialize ..."
SID="$(curl -s -D - "${AUTH_ARGS[@]}" -X POST "$MCP_URL" \
  -H "$CT" -H "Accept: $ACCEPT" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"mcp-curl-test","version":"1"}}}' \
  -o /dev/null | awk -F': ' 'tolower($1)=="mcp-session-id"{print $2}' | tr -d '\r')"

if [ -z "$SID" ]; then
  echo "ERROR: no mcp-session-id returned (check URL / auth)." >&2
  exit 1
fi
echo "   session: $SID"

# 2) notifications/initialized (required before tool calls).
echo "== [2/3] initialized ..."
curl -s "${AUTH_ARGS[@]}" -X POST "$MCP_URL" \
  -H "$CT" -H "Accept: $ACCEPT" -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' -o /dev/null

# Build the tool arguments.
if [ "$TOOL" = "check_gateway_health" ]; then
  ARGS_JSON='{}'
else
  ARGS_JSON="$(python3 -c 'import json,sys; print(json.dumps({"requirement": sys.argv[1]}))' "$REQUIREMENT")"
fi
BODY="$(python3 -c 'import json,sys; print(json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":sys.argv[1],"arguments":json.loads(sys.argv[2])}}))' "$TOOL" "$ARGS_JSON")"

# 3) tools/call — stream the SSE response to a log while echoing progress.
STAMP="$(date +%Y%m%d%H%M%S)"
LOG="$DOWNLOAD_DIR/mcp-test-$STAMP.sse.log"
mkdir -p "$DOWNLOAD_DIR"
echo "== [3/3] tools/call $TOOL (streaming; generate_prototype takes ~8-10 min) ..."
echo "   raw SSE log: $LOG"
curl -N -s "${AUTH_ARGS[@]}" -X POST "$MCP_URL" \
  -H "$CT" -H "Accept: $ACCEPT" -H "Mcp-Session-Id: $SID" \
  -d "$BODY" | tee "$LOG" | grep --line-buffered -E '"level"|"data"|"message"|serverInfo|DOWNLOAD_URL|artifacts' >/dev/null || true

echo "== stream finished."

# Extract the delivered ZIP download URL (save-agent artifact) from the log.
DL_URL="$(grep -oE 'https://[^"]+/api/saveagent/artifacts/[a-f0-9]{32}\.zip' "$LOG" | tail -1 || true)"
if [ -z "$DL_URL" ]; then
  echo "No download URL found in the stream. Inspect the log: $LOG" >&2
  # Show a short tail so the caller can see the tool result / any error.
  tail -c 1200 "$LOG" >&2 || true
  exit 0
fi

echo "== download URL: $DL_URL"
OUT="$DOWNLOAD_DIR/worldcup-bbc-$STAMP.zip"
if [ -z "${OPENCLAW_GATEWAY_TOKEN:-}" ]; then
  echo "OPENCLAW_GATEWAY_TOKEN not set; download manually:"
  echo "  curl -H 'Authorization: Bearer <token>' '$DL_URL' -o '$OUT'"
  exit 0
fi

code="$(curl -s -w '%{http_code}' -H "Authorization: Bearer $OPENCLAW_GATEWAY_TOKEN" "$DL_URL" -o "$OUT")"
echo "== downloaded ($code): $OUT"
if [ "$code" != "200" ]; then
  echo "ERROR: download failed (HTTP $code)." >&2
  exit 1
fi

# Extract into a local `code/` directory and open it in VS Code.
CODE_DIR="${CODE_DIR:-$REPO_ROOT/code}"
DEST="$CODE_DIR/project-$STAMP"
mkdir -p "$DEST"
if command -v unzip >/dev/null 2>&1; then
  unzip -q -o "$OUT" -d "$DEST"
  echo "== extracted to: $DEST"
  unzip -l "$OUT" | grep -vE '__pycache__|\.venv' | head -40
else
  echo "unzip not found; leaving the archive at $OUT" >&2
  exit 0
fi

# Open with VS Code Insiders, falling back to stable VS Code, then macOS `open`.
if command -v code-insiders >/dev/null 2>&1; then
  code-insiders "$DEST" && echo "== opened in VS Code Insiders"
elif command -v code >/dev/null 2>&1; then
  code "$DEST" && echo "== opened in VS Code"
elif command -v open >/dev/null 2>&1; then
  open -a "Visual Studio Code - Insiders" "$DEST" 2>/dev/null \
    || open -a "Visual Studio Code" "$DEST" 2>/dev/null \
    || echo "== open an editor manually: $DEST"
else
  echo "== open an editor manually: $DEST"
fi
