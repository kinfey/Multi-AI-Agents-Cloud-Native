#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# 03-deploy-qwen.sh
#
# Apply the ModelDeployment for Qwen3-0.6B served by KAITO's llama.cpp engine
# on CPU, and wait for it to come Ready.
# ----------------------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "$0")"/.. && pwd)"

kubectl apply -f "$HERE/airunway/modeldeployment-qwen-cpu.yaml"

echo "==> Waiting for ModelDeployment llama3-2-1b-cpu to become Ready (this may take 10-20 min on first pull)..."
kubectl -n airunway-models wait --for=condition=Ready modeldeployment/llama3-2-1b-cpu --timeout=30m || {
  echo "ModelDeployment did not become Ready. Showing diagnostics:"
  kubectl -n airunway-models describe modeldeployment/llama3-2-1b-cpu || true
  kubectl -n airunway-models get pods
  exit 1
}

echo "==> ModelDeployment is Ready. Service:"
kubectl -n airunway-models get svc llama3-2-1b-cpu

cat <<EOF

In-cluster OpenAI-compatible endpoint:
  http://llama3-2-1b-cpu.airunway-models.svc.cluster.local:8000/v1

Smoke test from a throwaway pod:

  kubectl -n airunway-models run smoke --rm -it --restart=Never \\
    --image=curlimages/curl -- \\
    -s http://llama3-2-1b-cpu:8000/v1/chat/completions \\
    -H 'content-type: application/json' \\
    -d '{"model":"Qwen/Qwen3-0.6B","messages":[{"role":"user","content":"Say hi in one word."}]}'

EOF
