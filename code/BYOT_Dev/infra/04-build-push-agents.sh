#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# 04-build-push-agents.sh
#
# Build the single agent image (used by all four roles) and push it to ACR.
# Auto-discovers the ACR in $RG if $ACR is not set.
# ----------------------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "$0")"/.. && pwd)"
IMAGE_NAME="${IMAGE_NAME:-byot-agent}"
IMAGE_TAG="${IMAGE_TAG:-v1}"

if [[ -z "${ACR_LOGIN_SERVER:-}" ]]; then
  if [[ -z "${ACR:-}" ]]; then
    : "${RG:?RG (resource group) must be set, or pass ACR=<name>}"
    ACR=$(az acr list -g "$RG" --query '[0].name' -o tsv)
    [[ -n "$ACR" ]] || { echo "No ACR found in $RG"; exit 1; }
  fi
  ACR_LOGIN_SERVER=$(az acr show -n "$ACR" --query loginServer -o tsv)
fi

echo "==> Building $IMAGE_NAME:$IMAGE_TAG via ACR Tasks ($ACR_LOGIN_SERVER)"
az acr build \
  --registry "${ACR_LOGIN_SERVER%%.*}" \
  --image "$IMAGE_NAME:$IMAGE_TAG" \
  --file "$HERE/agents/Dockerfile" \
  "$HERE/agents"

cat <<EOF

Image pushed:
  $ACR_LOGIN_SERVER/$IMAGE_NAME:$IMAGE_TAG

Next:
  export AGENT_IMAGE=$ACR_LOGIN_SERVER/$IMAGE_NAME:$IMAGE_TAG
  bash infra/05-deploy-agents.sh

EOF
