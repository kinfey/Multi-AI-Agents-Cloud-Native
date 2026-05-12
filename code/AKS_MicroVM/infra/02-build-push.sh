#!/usr/bin/env bash
# Build the container image and push it to ACR.
set -euo pipefail

# ACR can be provided explicitly, or resolved from RG when only one exists.
if [[ -z "${ACR:-}" ]]; then
  if [[ -z "${RG:-}" ]]; then
    echo "ERROR: Set ACR (e.g. export ACR=acrcopilotkata12345) or set RG to auto-discover ACR." >&2
    exit 1
  fi

  mapfile -t _acrs < <(az acr list -g "$RG" --query "[].name" -o tsv)
  if [[ "${#_acrs[@]}" -eq 0 ]]; then
    echo "ERROR: No ACR found in resource group '$RG'. Set ACR explicitly." >&2
    exit 1
  elif [[ "${#_acrs[@]}" -gt 1 ]]; then
    echo "ERROR: Multiple ACRs found in resource group '$RG': ${_acrs[*]}" >&2
    echo "Set ACR explicitly, e.g. export ACR=${_acrs[0]}" >&2
    exit 1
  fi

  ACR="${_acrs[0]}"
  echo "==> Auto-discovered ACR: $ACR (from RG=$RG)"
fi

IMAGE_TAG="${IMAGE_TAG:-v1}"
IMAGE_NAME="${IMAGE_NAME:-copilot-agent}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Building $IMAGE_NAME:$IMAGE_TAG in ACR $ACR (no local docker required)"
az acr build \
  --registry "$ACR" \
  --image "$IMAGE_NAME:$IMAGE_TAG" \
  --image "$IMAGE_NAME:latest" \
  --file Dockerfile \
  .

LOGIN_SERVER="$(az acr show -n "$ACR" --query loginServer -o tsv)"
echo "==> Pushed: $LOGIN_SERVER/$IMAGE_NAME:$IMAGE_TAG"
echo "Export for the deploy step:"
echo "  export IMAGE=$LOGIN_SERVER/$IMAGE_NAME:$IMAGE_TAG"
