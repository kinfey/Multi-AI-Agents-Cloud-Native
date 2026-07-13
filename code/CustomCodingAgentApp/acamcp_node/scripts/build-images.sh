#!/usr/bin/env bash
# Build and push the MCP server image to ACR.
#
# The workflow backend (acasbxapp_node) is the OpenClaw gateway, already
# deployed on Azure Container Apps and built from acasbxapp_node/docker/
# Dockerfile.openclaw (see acasbxapp_node/scripts/build-openclaw-image.sh).
# This script only builds the MCP server that talks to that gateway.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$SCRIPT_DIR/load-env.sh"
load_env_file "$MCP_ROOT/.env"

DOCKER_PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-}"

if [ -z "$ACR_LOGIN_SERVER" ] && [ "${ACR_NAME:-}" != "" ]; then
  ACR_LOGIN_SERVER="${ACR_NAME}.azurecr.io"
fi

MCP_IMAGE="${MCP_IMAGE:-}"
if [ -z "$MCP_IMAGE" ]; then
  if [ -n "$ACR_LOGIN_SERVER" ]; then
    MCP_IMAGE="${ACR_LOGIN_SERVER}/acamcp-server:${IMAGE_TAG}"
  else
    MCP_IMAGE="acamcp-server:${IMAGE_TAG}"
  fi
fi

echo "Building MCP server image: $MCP_IMAGE"
docker build --platform "$DOCKER_PLATFORM" \
  -f "$MCP_ROOT/docker/Dockerfile" \
  -t "$MCP_IMAGE" \
  "$MCP_ROOT"

if [ -n "$ACR_LOGIN_SERVER" ]; then
  az acr login --name "${ACR_NAME:-${ACR_LOGIN_SERVER%%.azurecr.io}}"
  docker push "$MCP_IMAGE"
fi

echo "MCP_IMAGE=$MCP_IMAGE"
