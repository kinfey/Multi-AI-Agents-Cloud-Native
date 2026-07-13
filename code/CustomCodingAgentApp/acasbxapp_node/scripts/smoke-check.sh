#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/load-env.sh"
load_env_file "$SCRIPT_DIR/../.env"

RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-kinfey}"
APP_NAME="${AZURE_OPENCLAW_PREFIX:-azure-openclaw-aca}-app"

FQDN="$(az containerapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --query properties.configuration.ingress.fqdn -o tsv)"

echo "OpenClaw gateway: https://${FQDN}"
az containerapp logs show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --tail 50

if [ "${OPENCLAW_GATEWAY_TOKEN:-}" != "" ]; then
  echo "Token URL: https://${FQDN}?token=${OPENCLAW_GATEWAY_TOKEN}"
else
  echo "Set OPENCLAW_GATEWAY_TOKEN to print the token URL."
fi
