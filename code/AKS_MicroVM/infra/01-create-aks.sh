#!/usr/bin/env bash
# Create an AKS cluster with Kata Containers (Pod Sandboxing) enabled.
#
# Prereqs:
#   - az CLI logged in (`az login`)
#   - aks-preview extension (auto-installed below)
#
# Reference:
#   https://learn.microsoft.com/azure/aks/use-pod-sandboxing
#   https://github.com/kata-containers/kata-containers
set -euo pipefail

RG="${RG:-rg-copilot-agent-kata}"
LOCATION="${LOCATION:-eastus}"
CLUSTER="${CLUSTER:-aks-copilot-agent-kata}"
ACR="${ACR:-acrcopilotkata$RANDOM}"
NODE_SIZE="${NODE_SIZE:-Standard_D4s_v3}"   # must support nested virtualization (eastus-safe default)
NODE_COUNT="${NODE_COUNT:-1}"

echo "==> Ensuring aks-preview extension is installed"
az extension add --name aks-preview --upgrade --only-show-errors

echo "==> Resource group: $RG ($LOCATION)"
az group create -n "$RG" -l "$LOCATION" -o none

echo "==> ACR: $ACR"
az acr create -g "$RG" -n "$ACR" --sku Basic -o none

echo "==> AKS cluster: $CLUSTER (Kata/Pod Sandboxing on AzureLinux)"
az aks create \
  -g "$RG" \
  -n "$CLUSTER" \
  --os-sku AzureLinux \
  --workload-runtime KataVmIsolation \
  --node-vm-size "$NODE_SIZE" \
  --node-count "$NODE_COUNT" \
  --enable-addons monitoring \
  --network-plugin azure \
  --network-policy calico \
  --attach-acr "$ACR" \
  --generate-ssh-keys \
  -o none

echo "==> Fetching kubeconfig"
az aks get-credentials -g "$RG" -n "$CLUSTER" --overwrite-existing

echo "==> Verify RuntimeClass"
kubectl get runtimeclass

cat <<EOF

Done. Save these for the next steps:

  export RG=$RG
  export CLUSTER=$CLUSTER
  export ACR=$ACR
  export ACR_LOGIN_SERVER=\$(az acr show -g $RG -n $ACR --query loginServer -o tsv)

EOF
