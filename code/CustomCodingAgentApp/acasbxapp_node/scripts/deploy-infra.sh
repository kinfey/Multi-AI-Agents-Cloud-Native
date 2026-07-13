#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/load-env.sh"
load_env_file "$SCRIPT_DIR/../.env"

RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-kinfey}"
LOCATION="${AZURE_LOCATION:-swedencentral}"
PREFIX="${AZURE_OPENCLAW_PREFIX:-azure-openclaw-aca}"
SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-4498459e-01d5-4a3f-b07e-8f1f36598c16}"

: "${OPENCLAW_IMAGE:?Set OPENCLAW_IMAGE to <acr-login-server>/openclaw:latest}"
: "${AZURE_AI_FOUNDRY_ENDPOINT:?Set AZURE_AI_FOUNDRY_ENDPOINT}"
: "${OPENCLAW_GATEWAY_TOKEN:?Set OPENCLAW_GATEWAY_TOKEN}"
: "${OPENCLAW_ACA_SANDBOX_ID:?Set OPENCLAW_ACA_SANDBOX_ID by running scripts/create-sandbox.sh}"

az account set --subscription "$SUBSCRIPTION_ID"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output table

DEPLOYMENT_OUTPUT="$(az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file infra/main.bicep \
  --parameters \
    location="$LOCATION" \
    prefix="$PREFIX" \
    acrName="${ACR_NAME:-azureopenclawaca}" \
    storageName="${STORAGE_ACCOUNT_NAME:-stazureopenclawaca}" \
    openclawImage="$OPENCLAW_IMAGE" \
    foundryEndpoint="$AZURE_AI_FOUNDRY_ENDPOINT" \
    foundryModelDeployment="${AZURE_AI_FOUNDRY_MODEL_DEPLOYMENT:-gpt-5.5}" \
    openclawGatewayToken="$OPENCLAW_GATEWAY_TOKEN" \
    acaSandboxId="$OPENCLAW_ACA_SANDBOX_ID" \
  --query properties.outputs.gatewayUrl.value \
  --output tsv)"

echo "OpenClaw gateway: $DEPLOYMENT_OUTPUT"
