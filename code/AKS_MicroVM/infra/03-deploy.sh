#!/usr/bin/env bash
# Apply Kubernetes manifests for the Kata-microVM-isolated Copilot agent.
#
# Required env:
#   IMAGE   — full image reference, e.g. myacr.azurecr.io/copilot-agent:v1
#
# Required files:
#   k8s/secret.yaml — copied from k8s/secret.example.yaml with a real
#                     GH_COPILOT_TOKEN.
set -euo pipefail

: "${IMAGE:?Set IMAGE to the full ACR image reference}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f k8s/secret.yaml ]]; then
  echo "ERROR: k8s/secret.yaml is missing." >&2
  echo "       cp k8s/secret.example.yaml k8s/secret.yaml and fill in the token." >&2
  exit 1
fi

echo "==> Applying namespace + secret"
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secret.yaml

echo "==> Applying RuntimeClass (idempotent — AKS may already manage it)"
kubectl apply -f k8s/runtimeclass.yaml || true

echo "==> Rendering deployment with IMAGE=$IMAGE"
tmpfile="$(mktemp)"
trap 'rm -f "$tmpfile"' EXIT
sed "s|REPLACE_ME.azurecr.io/copilot-agent:latest|$IMAGE|g" \
  k8s/deployment.yaml > "$tmpfile"

kubectl apply -f "$tmpfile"
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/networkpolicy.yaml

echo "==> Waiting for rollout"
kubectl -n copilot-agent rollout status deploy/copilot-agent --timeout=5m

echo "==> Pods:"
kubectl -n copilot-agent get pods -o wide

cat <<EOF

To test:
  kubectl -n copilot-agent port-forward svc/copilot-agent 8000:80
  curl -s http://localhost:8000/healthz
  curl -s -X POST http://localhost:8000/chat \\
    -H 'content-type: application/json' \\
    -d '{"message":"Call get_pod_info and tell me the kernel version"}'

EOF
