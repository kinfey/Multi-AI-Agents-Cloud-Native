#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# 02-install-airunway.sh
#
# Install:
#   1. AI Runway controller + CRDs (airunway.ai/v1alpha1)
#   2. KAITO provider shim (registers the kaito InferenceProviderConfig with AI Runway)
#   3. Upstream KAITO workspace controller via Helm
#
# KAITO is the only AI Runway provider with cpuSupport=true (engine: llamacpp),
# which is what lets us run Qwen3-0.6B on a CPU-only node.
#
# Refs:
#   https://github.com/kaito-project/airunway/blob/main/providers/kaito/deploy/kaito.yaml
#   https://github.com/kaito-project/kaito
# ----------------------------------------------------------------------------
set -euo pipefail

AIRUNWAY_REF="${AIRUNWAY_REF:-main}"
KAITO_HELM_VERSION="${KAITO_HELM_VERSION:-0.5.1}"

echo "==> Installing AI Runway controller (CRDs + controller-manager)"
kubectl apply -f "https://raw.githubusercontent.com/kaito-project/airunway/${AIRUNWAY_REF}/deploy/controller.yaml"

echo "==> Waiting for airunway-system controller to be Available"
kubectl -n airunway-system rollout status deploy/airunway-controller-manager --timeout=5m

echo "==> Installing KAITO provider shim for AI Runway"
kubectl apply -f "https://raw.githubusercontent.com/kaito-project/airunway/${AIRUNWAY_REF}/providers/kaito/deploy/kaito.yaml"

echo "==> Waiting for KAITO provider shim to be Available"
kubectl -n airunway-system rollout status deploy/airunway-kaito-provider --timeout=5m

echo "==> Installing upstream KAITO workspace controller via Helm"
helm repo add kaito https://azure.github.io/kaito/charts 2>/dev/null || true
helm repo update kaito
helm upgrade --install kaito-workspace kaito/workspace \
  --namespace kaito-workspace --create-namespace \
  --version "${KAITO_HELM_VERSION}" \
  --set clusterName="${CLUSTER:-aks-byot}" \
  --wait

echo "==> Creating the namespace that ModelDeployments will live in"
kubectl create namespace airunway-models --dry-run=client -o yaml | kubectl apply -f -

echo
echo "==> InferenceProviderConfigs registered with AI Runway:"
kubectl get inferenceproviderconfigs

cat <<EOF

AI Runway + KAITO are ready.

Next:
  bash infra/03-deploy-qwen.sh

EOF
