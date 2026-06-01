#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# 01-create-aks-kata.sh
#
# Create an AKS cluster with:
#   - AzureLinux OS (required for Pod Sandboxing)
#   - --workload-runtime KataVmIsolation (auto-creates kata-vm-isolation RuntimeClass)
#   - A VM SKU that supports nested virtualization (Standard_D4s_v3 by default)
#   - An attached ACR for the agent image
#
# Reference: https://learn.microsoft.com/azure/aks/use-pod-sandboxing
# ----------------------------------------------------------------------------
set -euo pipefail

RG="${RG:-rg-byot-aks}"
LOCATION="${LOCATION:-eastus}"
CLUSTER="${CLUSTER:-aks-byot}"
ACR="${ACR:-acrbyot$RANDOM}"
NODE_SIZE="${NODE_SIZE:-Standard_D4s_v3}"   # nested virt + 4 vCPU is enough for Qwen3-0.6B CPU
NODE_COUNT="${NODE_COUNT:-5}"               # 1 node for system + AI Runway model, 4 nodes one-per-agent

echo "==> Ensuring aks-preview extension is installed"
az extension add --name aks-preview --upgrade --only-show-errors

echo "==> Resource group: $RG ($LOCATION)"
az group create -n "$RG" -l "$LOCATION" -o none

echo "==> ACR: $ACR"
az acr create -g "$RG" -n "$ACR" --sku Basic -o none

echo "==> AKS cluster: $CLUSTER (KataVmIsolation on AzureLinux)"
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

echo "==> Verifying RuntimeClass (expect kata-vm-isolation)"
kubectl get runtimeclass

ACR_LOGIN_SERVER=$(az acr show -g "$RG" -n "$ACR" --query loginServer -o tsv)

cat <<EOF

Done. Export these for the following scripts:

  export RG=$RG
  export CLUSTER=$CLUSTER
  export ACR=$ACR
  export ACR_LOGIN_SERVER=$ACR_LOGIN_SERVER

EOF
