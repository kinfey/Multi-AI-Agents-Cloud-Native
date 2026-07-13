#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/load-env.sh"
load_env_file "$SCRIPT_DIR/../.env"

RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-kinfey}"
LOCATION="${AZURE_LOCATION:-westus}"
SANDBOX_GROUP="${AZURE_OPENCLAW_PREFIX:-azure-openclaw-aca}-sandbox"
ACA_INSTALL_DIR="${ACA_INSTALL_DIR:-$HOME/.local/bin}"
# User-assigned managed identity the sandbox (and its agents) authenticate as.
# Grants the deployment-agent an ARM/ACR token so it can build+deploy from inside
# the sandbox (fixes "Sandbox does not have an associated sandbox group with
# managed identity" / 401 unauthorized_client).
DEPLOY_MI_NAME="${OPENCLAW_ACA_DEPLOY_MI_NAME:-${AZURE_OPENCLAW_PREFIX:-azure-openclaw-aca}-deploy-mi}"
DEPLOY_MI_ACR="${OPENCLAW_ACA_DEPLOY_MI_ACR:-}"

detect_aca_platform() {
  local os arch os_tag arch_tag
  os="$(uname -s)"
  arch="$(uname -m)"

  case "$os" in
    Linux) os_tag="linux" ;;
    Darwin) os_tag="osx" ;;
    *)
      echo "Unsupported OS for aca CLI: $os" >&2
      exit 1
      ;;
  esac

  case "$arch" in
    x86_64|amd64)
      if [ "$os_tag" = "osx" ]; then
        echo "macOS x64 is not supported by the aca CLI preview." >&2
        exit 1
      fi
      arch_tag="x64"
      ;;
    aarch64|arm64)
      if [ "$os_tag" = "linux" ]; then
        echo "Linux ARM64 is not supported by the aca CLI preview." >&2
        exit 1
      fi
      arch_tag="arm64"
      ;;
    *)
      echo "Unsupported architecture for aca CLI: $arch" >&2
      exit 1
      ;;
  esac

  printf '%s-%s\n' "$os_tag" "$arch_tag"
}

install_aca_cli() {
  local platform version_file version expected_hash download_url tmp_dir archive actual_hash
  platform="$(detect_aca_platform)"
  version_file="$(curl -fsSL https://raw.githubusercontent.com/microsoft/azure-container-apps/main/aca-cli/preview/latest-version.txt)"
  version="$(printf '%s\n' "$version_file" | awk -F= '$1=="version"{print $2; exit}')"
  expected_hash="$(printf '%s\n' "$version_file" | awk -F= -v key="$platform" '$1==key{print $2; exit}')"

  if [ -z "$version" ] || [ -z "$expected_hash" ]; then
    echo "Failed to resolve aca CLI version metadata for platform $platform." >&2
    exit 1
  fi

  download_url="https://github.com/microsoft/azure-container-apps/releases/download/${version}/${version}-${platform}.tar.gz"
  tmp_dir="$(mktemp -d)"
  archive="$tmp_dir/aca.tar.gz"
  trap 'rm -rf "$tmp_dir"' RETURN

  curl -fsSL "$download_url" -o "$archive"
  if command -v sha256sum >/dev/null 2>&1; then
    actual_hash="$(sha256sum "$archive" | awk '{print $1}')"
  else
    actual_hash="$(shasum -a 256 "$archive" | awk '{print $1}')"
  fi
  if [ "$actual_hash" != "$expected_hash" ]; then
    echo "aca CLI checksum mismatch for $download_url" >&2
    exit 1
  fi

  mkdir -p "$ACA_INSTALL_DIR"
  tar -xzf "$archive" -C "$tmp_dir"
  install -m 0755 "$tmp_dir/aca" "$ACA_INSTALL_DIR/aca"
  export PATH="$ACA_INSTALL_DIR:$PATH"
}

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI is required. Install it, then run az login."
  exit 1
fi

if ! command -v aca >/dev/null 2>&1; then
  echo "Installing ACA Sandbox CLI."
  install_aca_cli
fi

SUBSCRIPTION_ID="$(az account show --query id -o tsv)"
PRINCIPAL_ID="$(az ad signed-in-user show --query id -o tsv)"

az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output table
aca sandboxgroup create \
  -g "$RESOURCE_GROUP" \
  --name "$SANDBOX_GROUP" \
  --location "$LOCATION" \
  -s "$SUBSCRIPTION_ID" \
  --set-config

aca sandboxgroup role create \
  --role "Container Apps SandboxGroup Data Owner" \
  --principal-id "$PRINCIPAL_ID"

# --- Deployment managed identity -------------------------------------------
# Create (idempotently) a user-assigned MI, grant it deploy permissions, and
# associate it with the sandbox group so agents running inside sandboxes can
# obtain ARM/ACR tokens via the local MSI endpoint (IDENTITY_ENDPOINT).
echo "Ensuring deployment managed identity: $DEPLOY_MI_NAME"
az identity create -g "$RESOURCE_GROUP" -n "$DEPLOY_MI_NAME" -l "$LOCATION" --output none
DEPLOY_MI_ID="$(az identity show -g "$RESOURCE_GROUP" -n "$DEPLOY_MI_NAME" --query id -o tsv)"
DEPLOY_MI_PRINCIPAL="$(az identity show -g "$RESOURCE_GROUP" -n "$DEPLOY_MI_NAME" --query principalId -o tsv)"

# Contributor on the resource group lets the agent create/update Container Apps.
az role assignment create \
  --assignee-object-id "$DEPLOY_MI_PRINCIPAL" \
  --assignee-principal-type ServicePrincipal \
  --role Contributor \
  --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP" \
  --output none 2>/dev/null || true

# AcrPush on the registry lets the agent push built images (az acr build/push).
if [ -n "$DEPLOY_MI_ACR" ]; then
  ACR_ID="$(az acr show -n "$DEPLOY_MI_ACR" -g "$RESOURCE_GROUP" --query id -o tsv 2>/dev/null || true)"
  if [ -n "$ACR_ID" ]; then
    az role assignment create \
      --assignee-object-id "$DEPLOY_MI_PRINCIPAL" \
      --assignee-principal-type ServicePrincipal \
      --role AcrPush \
      --scope "$ACR_ID" \
      --output none 2>/dev/null || true
  fi
fi

aca sandboxgroup identity assign \
  --group "$SANDBOX_GROUP" \
  --user-assigned "$DEPLOY_MI_ID" \
  -g "$RESOURCE_GROUP" \
  -s "$SUBSCRIPTION_ID" \
  --region "$LOCATION"

aca doctor
