#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/load-env.sh"
load_env_file "$SCRIPT_DIR/../.env"

PREFIX="${AZURE_OPENCLAW_PREFIX:-azure-openclaw-aca}"
WORK_DIR="${OPENCLAW_WORK_DIR:-.work/openclaw}"
IMAGE_TAG="${OPENCLAW_IMAGE_TAG:-openclaw:latest}"
DOCKER_PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"
ACR_LOGIN_SERVER="${ACR_LOGIN_SERVER:-}"
PUSH_IMAGE="${OPENCLAW_PUSH_IMAGE:-1}"
REMOTE_BUILD="${OPENCLAW_REMOTE_BUILD:-0}"

if [ -z "$ACR_LOGIN_SERVER" ] && [ "${OPENCLAW_IMAGE:-}" != "" ]; then
  ACR_LOGIN_SERVER="${OPENCLAW_IMAGE%%/*}"
fi

if [ -z "$ACR_LOGIN_SERVER" ] && [ "${ACR_NAME:-}" != "" ]; then
  ACR_LOGIN_SERVER="${ACR_NAME}.azurecr.io"
fi

mkdir -p .work

if [ ! -d "$WORK_DIR/.git" ]; then
  git clone --depth 1 --filter=blob:none https://github.com/openclaw/openclaw.git "$WORK_DIR"
elif [ "${OPENCLAW_UPDATE_SOURCE:-0}" = "1" ]; then
  git -C "$WORK_DIR" pull --ff-only
fi

mkdir -p "$WORK_DIR/docker"
cp docker/Dockerfile.openclaw "$WORK_DIR/Dockerfile.azure"
cp docker/openclaw.dockerignore "$WORK_DIR/.dockerignore"
cp docker/entrypoint.sh "$WORK_DIR/docker/entrypoint.sh"
mkdir -p "$WORK_DIR/custom/agents" "$WORK_DIR/custom/scripts"
rm -rf "$WORK_DIR/extensions/save-agent" "$WORK_DIR/custom/agents"
cp -R openclaw/extensions/save-agent "$WORK_DIR/extensions/save-agent"
cp -R openclaw/agents "$WORK_DIR/custom/agents"
cp scripts/save-agent-pack.sh "$WORK_DIR/custom/scripts/save-agent-pack"
cp scripts/deploy-agent-to-aca.sh "$WORK_DIR/custom/scripts/deploy-agent-to-aca"
chmod +x "$WORK_DIR/docker/entrypoint.sh"
chmod +x "$WORK_DIR/custom/scripts/save-agent-pack" "$WORK_DIR/custom/scripts/deploy-agent-to-aca"

# Apply local source patches (idempotent). Injects SSE keepalive so long agent
# turns survive the ingress idle timeout (Azure Container Apps closes idle
# connections after 240s).
python3 "$SCRIPT_DIR/patch-openclaw-source.py" "$WORK_DIR"

if [ "$REMOTE_BUILD" = "1" ]; then
  : "${ACR_NAME:?Set ACR_NAME for an ACR remote build}"
  az acr build \
    --registry "$ACR_NAME" \
    --image openclaw:latest \
    --platform "$DOCKER_PLATFORM" \
    --file "$WORK_DIR/Dockerfile.azure" \
    "$WORK_DIR"
  echo "${ACR_LOGIN_SERVER:-${ACR_NAME}.azurecr.io}/openclaw:latest"
  exit 0
fi

docker build --pull=false --platform "$DOCKER_PLATFORM" -f "$WORK_DIR/Dockerfile.azure" -t "$IMAGE_TAG" "$WORK_DIR"

if [ "$PUSH_IMAGE" = "1" ] && [ -n "$ACR_LOGIN_SERVER" ]; then
  az acr login --name "${ACR_NAME:-${ACR_LOGIN_SERVER%%.azurecr.io}}"
  docker tag "$IMAGE_TAG" "$ACR_LOGIN_SERVER/openclaw:latest"
  docker push "$ACR_LOGIN_SERVER/openclaw:latest"
  echo "$ACR_LOGIN_SERVER/openclaw:latest"
else
  echo "$IMAGE_TAG"
  if [ "$PUSH_IMAGE" = "1" ]; then
    echo "Set ACR_LOGIN_SERVER to tag and push to Azure Container Registry."
  else
    echo "Image push disabled by OPENCLAW_PUSH_IMAGE=0."
  fi
fi
