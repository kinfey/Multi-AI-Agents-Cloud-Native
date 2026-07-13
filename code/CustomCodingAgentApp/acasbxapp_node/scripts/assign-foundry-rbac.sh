#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/load-env.sh"
load_env_file "$SCRIPT_DIR/../.env"

RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-kinfey}"
APP_NAME="${AZURE_OPENCLAW_PREFIX:-azure-openclaw-aca}-app"

: "${AZURE_AI_FOUNDRY_RESOURCE_ID:?Set AZURE_AI_FOUNDRY_RESOURCE_ID to the Foundry or Azure AI resource ID}"

PRINCIPAL_ID="${CONTAINER_APP_PRINCIPAL_ID:-$(az containerapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --query identity.principalId -o tsv)}"

az role assignment create \
  --assignee-object-id "$PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "Cognitive Services User" \
  --scope "$AZURE_AI_FOUNDRY_RESOURCE_ID"
