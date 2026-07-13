#!/usr/bin/env bash
# Deploy the OpenClaw gateway (acasbxapp_node) to AKS.
#
# The gateway authenticates to Microsoft Foundry using the AKS kubelet managed
# identity (granted Cognitive Services User / OpenAI User on the Foundry
# resource). Gateway env is sourced from acasbxapp_node/.env.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SBX_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
K8S_DIR="$SBX_ROOT/k8s"

source "$SCRIPT_DIR/load-env.sh"
load_env_file "$SBX_ROOT/.env"

: "${OPENCLAW_GATEWAY_TOKEN:?Set OPENCLAW_GATEWAY_TOKEN in acasbxapp_node/.env}"
: "${AZURE_AI_FOUNDRY_ENDPOINT:?Set AZURE_AI_FOUNDRY_ENDPOINT}"

AZURE_RESOURCE_GROUP="${AZURE_RESOURCE_GROUP:-rg-kinfey}"
AZURE_LOCATION="${AZURE_LOCATION:-swedencentral}"
AKS_CLUSTER_NAME="${AKS_CLUSTER_NAME:-kinfey-aks-openclaw-cluster}"
NAMESPACE="${K8S_NAMESPACE:-openclaw}"

# OpenClaw gateway image (built by scripts/build-openclaw-image.sh).
GATEWAY_IMAGE="${GATEWAY_IMAGE:-${OPENCLAW_IMAGE:-azureopenclawaca20260706.azurecr.io/openclaw:latest}}"

# --- Force a fresh image pull on redeploy -------------------------------------
# The gateway Deployment uses imagePullPolicy: IfNotPresent, so an unchanged tag
# (e.g. ':latest') will not be re-pulled. Import the freshly built image to a
# unique timestamp tag inside ACR and deploy that, guaranteeing a clean rollout.
# Set DEPLOY_UNIQUE_TAG=0 to deploy GATEWAY_IMAGE verbatim.
if [ "${DEPLOY_UNIQUE_TAG:-1}" = "1" ]; then
  _registry="${GATEWAY_IMAGE%%/*}"          # e.g. azureopenclawaca20260706.azurecr.io
  _acr_name="${ACR_NAME:-${_registry%%.*}}" # e.g. azureopenclawaca20260706
  _repo_ref="${GATEWAY_IMAGE##*/}"          # e.g. openclaw:latest
  _repo="${_repo_ref%%:*}"                  # e.g. openclaw
  _deploy_tag="$(date +%Y%m%d%H%M%S)"
  echo "Importing ${GATEWAY_IMAGE} -> ${_repo}:${_deploy_tag} (unique tag for a clean rollout) ..."
  if az acr import --name "$_acr_name" --source "$GATEWAY_IMAGE" \
       --image "${_repo}:${_deploy_tag}" --force -o none 2>/dev/null; then
    GATEWAY_IMAGE="${_registry}/${_repo}:${_deploy_tag}"
  else
    echo "WARN: ACR import failed; deploying ${GATEWAY_IMAGE} as-is (may not re-pull)." >&2
  fi
fi
echo "Deploying gateway image: $GATEWAY_IMAGE"

echo "Fetching AKS credentials for $AKS_CLUSTER_NAME ..."
az aks get-credentials -g "$AZURE_RESOURCE_GROUP" -n "$AKS_CLUSTER_NAME" --overwrite-existing

# Kubelet managed identity client id (used by `az login --identity`).
KUBELET_CLIENT_ID="$(az aks show -g "$AZURE_RESOURCE_GROUP" -n "$AKS_CLUSTER_NAME" \
  --query identityProfile.kubeletidentity.clientId -o tsv)"
echo "Kubelet identity client id: $KUBELET_CLIENT_ID"

# --- Grant the kubelet MI access to the ACA sandbox group ---------------------
# The gateway runs agents in an ACA sandbox via the `aca` CLI, authenticating as
# the kubelet managed identity (`az login --identity`). That identity therefore
# needs the "Container Apps SandboxGroup Data Owner" role on the sandbox group,
# otherwise every agent exec/read/write fails with a 403. Idempotent.
AZURE_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-$(az account show --query id -o tsv 2>/dev/null)}"
if [ -n "${OPENCLAW_ACA_SANDBOX_GROUP:-}" ] && command -v aca >/dev/null 2>&1; then
  KUBELET_PRINCIPAL_ID="$(az aks show -g "$AZURE_RESOURCE_GROUP" -n "$AKS_CLUSTER_NAME" \
    --query identityProfile.kubeletidentity.objectId -o tsv 2>/dev/null)"
  if [ -n "$KUBELET_PRINCIPAL_ID" ]; then
    echo "Granting sandbox-group Data Owner to kubelet MI ($KUBELET_PRINCIPAL_ID) ..."
    ACA_SUBSCRIPTION="$AZURE_SUBSCRIPTION_ID" ACA_RESOURCE_GROUP="$AZURE_RESOURCE_GROUP" \
    ACA_REGION="$AZURE_LOCATION" \
    aca sandboxgroup role create \
      --role "Container Apps SandboxGroup Data Owner" \
      --principal-id "$KUBELET_PRINCIPAL_ID" \
      --group "$OPENCLAW_ACA_SANDBOX_GROUP" 2>/dev/null \
      && echo "  role assignment ensured" \
      || echo "  WARN: role grant skipped/failed (may already exist or lack permission)."
  fi
fi

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

# Gateway environment as a Kubernetes secret.
kubectl -n "$NAMESPACE" create secret generic openclaw-gateway-env \
  --from-literal=OPENCLAW_GATEWAY_TOKEN="$OPENCLAW_GATEWAY_TOKEN" \
  --from-literal=AZURE_AI_FOUNDRY_ENDPOINT="$AZURE_AI_FOUNDRY_ENDPOINT" \
  --from-literal=AZURE_AI_FOUNDRY_MODEL_DEPLOYMENT="${AZURE_AI_FOUNDRY_MODEL_DEPLOYMENT:-gpt-5.5}" \
  --from-literal=AZURE_RESOURCE_GROUP="$AZURE_RESOURCE_GROUP" \
  --from-literal=AZURE_LOCATION="$AZURE_LOCATION" \
  --from-literal=OPENCLAW_ACA_SANDBOX_ID="${OPENCLAW_ACA_SANDBOX_ID:-}" \
  --from-literal=OPENCLAW_ACA_SANDBOX_GROUP="${OPENCLAW_ACA_SANDBOX_GROUP:-}" \
  --from-literal=ACA_SUBSCRIPTION="${AZURE_SUBSCRIPTION_ID:-}" \
  --from-literal=ACA_RESOURCE_GROUP="$AZURE_RESOURCE_GROUP" \
  --from-literal=ACA_REGION="$AZURE_LOCATION" \
  --from-literal=ACA_SANDBOX_GROUP="${OPENCLAW_ACA_SANDBOX_GROUP:-}" \
  --dry-run=client -o yaml | kubectl apply -f -

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
sed \
  -e "s|__GATEWAY_IMAGE__|${GATEWAY_IMAGE}|g" \
  -e "s|__KUBELET_CLIENT_ID__|${KUBELET_CLIENT_ID}|g" \
  "$K8S_DIR/gateway.yaml" > "$TMP"

kubectl apply -f "$TMP"

echo "Waiting for rollout ..."
kubectl -n "$NAMESPACE" rollout status deploy/acasbxapp-gateway --timeout=240s

echo "Deployed. Gateway resources:"
kubectl -n "$NAMESPACE" get deploy,svc,ingress -l app.kubernetes.io/name=acasbxapp-gateway
