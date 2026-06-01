#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# 06-show-mcp-endpoints.sh
#
# The four agent Services are now `type: LoadBalancer`, so each one gets a
# public Azure IP. Use those URLs directly in VS Code Copilot Chat — no
# `kubectl proxy` and no localhost needed.
# ----------------------------------------------------------------------------
set -euo pipefail

NAMESPACE="${NAMESPACE:-agents}"
ROLES=(byot-requirements byot-code byot-test byot-deploy)

echo "==> Waiting for LoadBalancer IPs in namespace $NAMESPACE..."
for svc in "${ROLES[@]}"; do
  for _ in $(seq 1 60); do
    ip=$(kubectl -n "$NAMESPACE" get svc "$svc" \
      -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
    if [[ -n "$ip" ]]; then
      printf '%-22s %s\n' "$svc" "$ip"
      break
    fi
    sleep 5
  done
  if [[ -z "${ip:-}" ]]; then
    echo "ERROR: $svc has no LoadBalancer IP yet." >&2
    exit 1
  fi
done

echo
echo "==> MCP endpoints (use these in VS Code Copilot Chat):"
for svc in "${ROLES[@]}"; do
  ip=$(kubectl -n "$NAMESPACE" get svc "$svc" \
    -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
  printf '  %-22s http://%s/mcp\n' "$svc" "$ip"
done
