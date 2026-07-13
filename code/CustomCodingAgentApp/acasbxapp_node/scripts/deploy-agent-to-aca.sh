#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT="${OPENCLAW_CODING_WORKSPACE:-/state/openclaw/workspaces/coding-agent}"
SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:?AZURE_SUBSCRIPTION_ID is required}"
RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:?AZURE_RESOURCE_GROUP is required}"
LOCATION="${AZURE_LOCATION:?AZURE_LOCATION is required}"
ACR_NAME="${ACR_NAME:?ACR_NAME is required}"
CONTAINER_APP_ENVIRONMENT="${CONTAINER_APP_ENVIRONMENT:?CONTAINER_APP_ENVIRONMENT is required}"
PULL_IDENTITY_ID="${OPENCLAW_DEPLOY_IDENTITY_ID:?OPENCLAW_DEPLOY_IDENTITY_ID is required}"

source_path=""
app_name=""
target_port="8080"
health_path="/"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --source)
      source_path="${2:-}"
      shift 2
      ;;
    --name)
      app_name="${2:-}"
      shift 2
      ;;
    --target-port)
      target_port="${2:-}"
      shift 2
      ;;
    --health-path)
      health_path="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if ! [[ "$app_name" =~ ^[a-z][a-z0-9-]{1,30}[a-z0-9]$ ]]; then
  echo "--name must be 3-32 lowercase letters, numbers, or hyphens" >&2
  exit 2
fi
if ! [[ "$target_port" =~ ^[0-9]+$ ]] || [ "$target_port" -lt 1 ] || [ "$target_port" -gt 65535 ]; then
  echo "--target-port must be between 1 and 65535" >&2
  exit 2
fi
if [[ "$health_path" != /* ]] || [[ "$health_path" == *[[:space:]?#]* ]]; then
  echo "--health-path must be an absolute URL path without a query string" >&2
  exit 2
fi
if [ -z "$source_path" ]; then
  echo "--source is required" >&2
  exit 2
fi

source_root="$(realpath "$SOURCE_ROOT")"
source_dir="$(realpath "$source_path")"
case "$source_dir/" in
  "$source_root"/*)
    ;;
  *)
    echo "--source must be inside $source_root" >&2
    exit 2
    ;;
esac
if [ ! -f "$source_dir/Dockerfile" ]; then
  echo "A Dockerfile is required at $source_dir/Dockerfile" >&2
  exit 2
fi
if find "$source_dir" -type l -print -quit | grep -q .; then
  echo "Symbolic links are not allowed in deployment source" >&2
  exit 2
fi

for command_name in az curl; do
  command -v "$command_name" >/dev/null || {
    echo "Required command is unavailable: $command_name" >&2
    exit 1
  }
done

az account set --subscription "$SUBSCRIPTION_ID"
registry_server="$(az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --query loginServer -o tsv)"
image_tag="$(date -u +%Y%m%d%H%M%S)-$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
image="$registry_server/$app_name:$image_tag"

previous_revision=""
previous_image=""
if az containerapp show --name "$app_name" --resource-group "$RESOURCE_GROUP" --output none 2>/dev/null; then
  previous_revision="$(az containerapp show --name "$app_name" --resource-group "$RESOURCE_GROUP" --query properties.latestReadyRevisionName -o tsv)"
  previous_image="$(az containerapp show --name "$app_name" --resource-group "$RESOURCE_GROUP" --query properties.template.containers[0].image -o tsv)"
fi

az acr build \
  --registry "$ACR_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --image "$app_name:$image_tag" \
  "$source_dir" \
  --output none

revision_suffix="r${image_tag//[^a-zA-Z0-9]/}"
revision_suffix="${revision_suffix:0:20}"
if [ -n "$previous_revision" ]; then
  az containerapp identity assign \
    --name "$app_name" \
    --resource-group "$RESOURCE_GROUP" \
    --user-assigned "$PULL_IDENTITY_ID" \
    --output none
  az containerapp registry set \
    --name "$app_name" \
    --resource-group "$RESOURCE_GROUP" \
    --server "$registry_server" \
    --identity "$PULL_IDENTITY_ID" \
    --output none
  az containerapp update \
    --name "$app_name" \
    --resource-group "$RESOURCE_GROUP" \
    --image "$image" \
    --revision-suffix "$revision_suffix" \
    --output none
else
  az containerapp create \
    --name "$app_name" \
    --resource-group "$RESOURCE_GROUP" \
    --environment "$CONTAINER_APP_ENVIRONMENT" \
    --image "$image" \
    --location "$LOCATION" \
    --user-assigned "$PULL_IDENTITY_ID" \
    --registry-server "$registry_server" \
    --registry-identity "$PULL_IDENTITY_ID" \
    --ingress external \
    --target-port "$target_port" \
    --transport auto \
    --min-replicas 1 \
    --max-replicas 3 \
    --revision-suffix "$revision_suffix" \
    --output none
fi

deployment_state="$(az containerapp show --name "$app_name" --resource-group "$RESOURCE_GROUP" --query properties.provisioningState -o tsv)"
revision="$(az containerapp show --name "$app_name" --resource-group "$RESOURCE_GROUP" --query properties.latestReadyRevisionName -o tsv)"
fqdn="$(az containerapp show --name "$app_name" --resource-group "$RESOURCE_GROUP" --query properties.configuration.ingress.fqdn -o tsv)"
if [ "$deployment_state" != "Succeeded" ] || [ -z "$revision" ] || [ -z "$fqdn" ]; then
  echo "Container App did not reach a ready state" >&2
  exit 1
fi

url="https://$fqdn"
curl --fail --silent --show-error --location \
  --retry 12 --retry-all-errors --retry-delay 5 \
  --max-time 30 "$url$health_path" >/dev/null

printf '{"status":"succeeded","url":"%s","image":"%s","revision":"%s","previousRevision":"%s","previousImage":"%s"}\n' \
  "$url" "$image" "$revision" "$previous_revision" "$previous_image"