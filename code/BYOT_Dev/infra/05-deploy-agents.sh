#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# 05-deploy-agents.sh
#
# Apply namespace, configmap, four Kata-isolated Deployments, Services, and
# the NetworkPolicy. Substitutes the agent image into each Deployment.
# ----------------------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "$0")"/.. && pwd)"

: "${AGENT_IMAGE:?AGENT_IMAGE must be set, e.g. acrbyotXXXX.azurecr.io/byot-agent:v1}"

echo "==> Applying namespace + configmap"
kubectl apply -f "$HERE/k8s/namespace.yaml"
kubectl apply -f "$HERE/k8s/configmap.yaml"

for role in requirements code test deploy; do
  echo "==> Rendering and applying Deployment for byot-$role"
  sed "s|REPLACE_WITH_AGENT_IMAGE|$AGENT_IMAGE|g" \
    "$HERE/k8s/deployment-${role}.yaml" | kubectl apply -f -
done

echo "==> Applying Services"
kubectl apply -f "$HERE/k8s/services.yaml"

echo "==> Applying NetworkPolicy"
kubectl apply -f "$HERE/k8s/networkpolicy.yaml"

echo "==> Waiting for rollouts..."
for role in requirements code test deploy; do
  kubectl -n agents rollout status deploy/byot-$role --timeout=5m
done

echo
echo "==> Pods (one per node, scheduled by podAntiAffinity):"
kubectl -n agents get pods -o wide

echo
echo "==> Distinct nodes hosting BYOT agents (expect 4):"
kubectl -n agents get pods -l app.kubernetes.io/part-of=byot \
  -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' | sort -u

cat <<EOF

Agents deployed. To make them reachable from VS Code / Copilot:

  bash infra/06-mcp-proxy.sh   # keep this running in a separate terminal

Then open VS Code in this folder; .vscode/mcp.json registers all four MCP servers.

EOF
