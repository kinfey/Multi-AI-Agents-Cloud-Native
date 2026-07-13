#!/usr/bin/env bash
# Deploy the MCP server to AKS. It calls the OpenClaw gateway (acasbxapp_node)
# that is already running on Azure Container Apps.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MCP_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$MCP_ROOT/.." && pwd)"
K8S_DIR="$MCP_ROOT/k8s"

source "$SCRIPT_DIR/load-env.sh"
load_env_file "$MCP_ROOT/.env"
# Fall back to the gateway token from the acasbxapp_node .env if not set here.
load_env_file "$REPO_ROOT/acasbxapp_node/.env"

: "${AZURE_RESOURCE_GROUP:?Set AZURE_RESOURCE_GROUP in .env}"
: "${AKS_CLUSTER_NAME:?Set AKS_CLUSTER_NAME in .env}"
: "${MCP_IMAGE:?Set MCP_IMAGE (run build-images.sh first)}"
: "${OPENCLAW_GATEWAY_TOKEN:?Set OPENCLAW_GATEWAY_TOKEN (gateway bearer token)}"

NAMESPACE="${K8S_NAMESPACE:-openclaw}"
MCP_HOST="${MCP_HOST:-}"

# --- Force a fresh image pull on redeploy -------------------------------------
# Kubernetes will not re-pull an image whose tag is unchanged (e.g. ':latest'),
# so import the freshly built image to a unique timestamp tag inside ACR and
# deploy that instead. This guarantees a clean rollout every time.
# Set DEPLOY_UNIQUE_TAG=0 to deploy MCP_IMAGE verbatim.
if [ "${DEPLOY_UNIQUE_TAG:-1}" = "1" ] && [ -n "${ACR_NAME:-}" ]; then
  _registry="${ACR_LOGIN_SERVER:-${ACR_NAME}.azurecr.io}"
  _repo_ref="${MCP_IMAGE##*/}"      # e.g. acamcp-server:latest
  _repo="${_repo_ref%%:*}"          # e.g. acamcp-server
  _deploy_tag="$(date +%Y%m%d%H%M%S)"
  echo "Importing ${MCP_IMAGE} -> ${_repo}:${_deploy_tag} (unique tag for a clean rollout) ..."
  if az acr import --name "$ACR_NAME" --source "$MCP_IMAGE" \
       --image "${_repo}:${_deploy_tag}" --force -o none 2>/dev/null; then
    MCP_IMAGE="${_registry}/${_repo}:${_deploy_tag}"
  else
    echo "WARN: ACR import failed; deploying ${MCP_IMAGE} as-is (may not re-pull)." >&2
  fi
fi
echo "Deploying MCP image: $MCP_IMAGE"

echo "Fetching AKS credentials for $AKS_CLUSTER_NAME ..."
az aks get-credentials \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AKS_CLUSTER_NAME" \
  --overwrite-existing

kubectl apply -f "$K8S_DIR/namespace.yaml"

# Gateway bearer token as a Kubernetes secret (never baked into the image).
kubectl -n "$NAMESPACE" create secret generic acamcp-gateway \
  --from-literal=gateway-token="$OPENCLAW_GATEWAY_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -

# --- HTTP Basic auth for the public MCP ingress -------------------------------
# The ingress enforces Basic auth via the `acamcp-basic-auth` secret. Provide
# MCP_BASIC_AUTH_USER / MCP_BASIC_AUTH_PASSWORD to (re)create it here; if the
# password is omitted an existing secret is kept as-is.
CERT_ISSUER="${CERT_ISSUER:-letsencrypt-prod}"
MCP_BASIC_AUTH_USER="${MCP_BASIC_AUTH_USER:-mcp}"
if [ -n "${MCP_BASIC_AUTH_PASSWORD:-}" ]; then
  if command -v htpasswd >/dev/null 2>&1; then
    HTLINE="$(htpasswd -nbB "$MCP_BASIC_AUTH_USER" "$MCP_BASIC_AUTH_PASSWORD")"
  else
    HTLINE="${MCP_BASIC_AUTH_USER}:$(openssl passwd -apr1 "$MCP_BASIC_AUTH_PASSWORD")"
  fi
  printf '%s\n' "$HTLINE" | kubectl -n "$NAMESPACE" create secret generic acamcp-basic-auth \
    --from-file=auth=/dev/stdin --dry-run=client -o yaml | kubectl apply -f -
  echo "Basic auth secret updated for user '$MCP_BASIC_AUTH_USER'."
elif ! kubectl -n "$NAMESPACE" get secret acamcp-basic-auth >/dev/null 2>&1; then
  echo "WARN: acamcp-basic-auth secret missing and MCP_BASIC_AUTH_PASSWORD not set;" >&2
  echo "      the ingress will return 503 until the auth secret exists." >&2
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

for f in acamcp-server.yaml ingress.yaml; do
  sed \
    -e "s|__MCP_IMAGE__|${MCP_IMAGE}|g" \
    -e "s|__MCP_HOST__|${MCP_HOST}|g" \
    -e "s|__CERT_ISSUER__|${CERT_ISSUER}|g" \
    "$K8S_DIR/$f" > "$TMP_DIR/$f"
done

kubectl apply -f "$TMP_DIR/acamcp-server.yaml"

if [ -n "$MCP_HOST" ]; then
  kubectl apply -f "$TMP_DIR/ingress.yaml"
else
  echo "MCP_HOST is empty; skipping public ingress (use kubectl port-forward instead)." >&2
fi

echo "Waiting for rollout ..."
kubectl -n "$NAMESPACE" rollout status deploy/acamcp-server --timeout=180s

echo "Deployed. Resources in namespace '$NAMESPACE':"
kubectl -n "$NAMESPACE" get deploy,svc,ingress
