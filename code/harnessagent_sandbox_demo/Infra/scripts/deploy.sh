#!/usr/bin/env bash
# Apply the Kustomize bundle. Run after the cluster is prepared (see README).
#
# Usage:
#   ACR_NAME=myregistry IMAGE_TAG=v0.1.0 ./scripts/deploy.sh
set -euo pipefail

ACR_NAME="${ACR_NAME:?set ACR_NAME}"
IMAGE_NAME="${IMAGE_NAME:-fifa-2026-podcast}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

K8S_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../k8s" && pwd)"
cd "${K8S_DIR}"

# Patch the image reference in the kustomization on the fly.
kustomize edit set image \
    "REPLACE-ME.azurecr.io/${IMAGE_NAME}=${ACR_NAME}.azurecr.io/${IMAGE_NAME}:${IMAGE_TAG}"

echo "==> kubectl apply -k ${K8S_DIR}"
kubectl apply -k "${K8S_DIR}"

# Restore the placeholder so the diff stays clean in git.
kustomize edit set image "${ACR_NAME}.azurecr.io/${IMAGE_NAME}=REPLACE-ME.azurecr.io/${IMAGE_NAME}:latest"

echo "==> Deployed."
echo "  Watch CronJob:  kubectl -n podcast-pipeline get cronjob podcast-daily -w"
echo "  Run once now:   kubectl -n podcast-pipeline create job --from=cronjob/podcast-daily podcast-manual-$(date +%s)"
