#!/usr/bin/env bash
# Build and push the podcast pipeline image to an Azure Container Registry.
#
# Uses `az acr build` (server-side build inside ACR) so no local Docker
# daemon is required. The image installs hyperlight-sandbox from PyPI and
# extracts the prebuilt python-sandbox.aot during the build, so cold
# builds take roughly 3 minutes.
#
# Usage:
#   ACR_NAME=myregistry RG=my-rg IMAGE_TAG=v0.1.0 ./Infra/scripts/build-and-push.sh
set -euo pipefail

ACR_NAME="${ACR_NAME:?set ACR_NAME (the registry short name, e.g. 'myregistry')}"
RG="${RG:?set RG (the resource group that contains the ACR)}"
IMAGE_NAME="${IMAGE_NAME:-fifa-2026-podcast}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

echo "==> az acr build ${ACR_NAME}.azurecr.io/${IMAGE_NAME}:${IMAGE_TAG}"
az acr build \
    --registry "${ACR_NAME}" \
    --resource-group "${RG}" \
    --image "${IMAGE_NAME}:${IMAGE_TAG}" \
    --file Infra/Dockerfile \
    .

echo "==> Last 3 runs:"
az acr task list-runs -r "${ACR_NAME}" --top 3 -o table

echo "==> Done: ${ACR_NAME}.azurecr.io/${IMAGE_NAME}:${IMAGE_TAG}"
