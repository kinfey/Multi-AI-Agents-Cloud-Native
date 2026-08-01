#!/bin/sh
set -eu

: "${AZURE_CONTAINER_REGISTRY_ENDPOINT:?Set AZURE_CONTAINER_REGISTRY_ENDPOINT}"
: "${AKS_WORKLOAD_IDENTITY_CLIENT_ID:?Set AKS_WORKLOAD_IDENTITY_CLIENT_ID}"
: "${AZURE_KEY_VAULT_NAME:?Set AZURE_KEY_VAULT_NAME}"
: "${AZURE_SUBSCRIPTION_ID:?Set AZURE_SUBSCRIPTION_ID}"
: "${AZURE_TENANT_ID:?Set AZURE_TENANT_ID}"
: "${AZURE_RESOURCE_GROUP:?Set AZURE_RESOURCE_GROUP}"
: "${AKS_CLUSTER_NAME:?Set AKS_CLUSTER_NAME}"
: "${AZURE_FOUNDRY_PROJECT_ENDPOINT:?Set AZURE_FOUNDRY_PROJECT_ENDPOINT}"
: "${AZURE_TEXT_MODEL:?Set AZURE_TEXT_MODEL}"

KARS_VERSION=${KARS_VERSION:-v0.1.25}
KARS_CHART_PATH=${KARS_CHART_PATH:-}
image_tag=${KARS_VERSION#v}
registry_name=${AZURE_CONTAINER_REGISTRY_ENDPOINT%%.*}

ensure_release_image() {
    repository=$1
    if ! az acr repository show \
        --name "$registry_name" \
        --image "$repository:$image_tag" \
        --output none 2>/dev/null; then
        az acr import \
            --name "$registry_name" \
            --source "ghcr.io/azure/$repository:$KARS_VERSION" \
            --image "$repository:$image_tag"
    fi
}

if [ -z "$KARS_CHART_PATH" ]; then
    command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 1; }
    archive_dir=$(mktemp -d)
    trap 'rm -rf "$archive_dir"' EXIT INT TERM
    curl -fsSL "https://github.com/Azure/kars/archive/refs/tags/${KARS_VERSION}.tar.gz" \
        | tar -xz -C "$archive_dir"
    KARS_CHART_PATH="$archive_dir/kars-${KARS_VERSION#v}/deploy/helm/kars"
fi

[ -f "$KARS_CHART_PATH/Chart.yaml" ] || {
    echo "KARS Helm chart not found at $KARS_CHART_PATH" >&2
    exit 1
}

ensure_release_image kars-controller
ensure_release_image kars-inference-router
ensure_release_image kars-conformance-runner
ensure_release_image openclaw-sandbox

identity_name=$(az identity list \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --query "[?clientId=='$AKS_WORKLOAD_IDENTITY_CLIENT_ID'].name | [0]" -o tsv)
oidc_issuer=$(az aks show \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$AKS_CLUSTER_NAME" \
    --query oidcIssuerProfile.issuerUrl -o tsv)

helm upgrade --install kars "$KARS_CHART_PATH" \
    --namespace kars-system \
    --create-namespace \
    --wait \
    --set "controller.image.repository=$AZURE_CONTAINER_REGISTRY_ENDPOINT/kars-controller" \
    --set "controller.image.tag=$image_tag" \
    --set "controller.image.pullPolicy=IfNotPresent" \
    --set "inferenceRouter.image.repository=$AZURE_CONTAINER_REGISTRY_ENDPOINT/kars-inference-router" \
    --set "inferenceRouter.image.tag=$image_tag" \
    --set "inferenceRouter.image.pullPolicy=IfNotPresent" \
    --set "sandbox.image.repository=$AZURE_CONTAINER_REGISTRY_ENDPOINT/openclaw-sandbox" \
    --set "sandbox.image.tag=$image_tag" \
    --set "sandbox.image.pullPolicy=IfNotPresent" \
    --set "azure.workloadIdentity.clientId=$AKS_WORKLOAD_IDENTITY_CLIENT_ID" \
    --set "azure.keyVaultCsi.keyVaultName=$AZURE_KEY_VAULT_NAME" \
    --set "fedcred.subscriptionId=$AZURE_SUBSCRIPTION_ID" \
    --set "fedcred.tenantId=$AZURE_TENANT_ID" \
    --set "fedcred.identityName=$identity_name" \
    --set "fedcred.identityResourceGroup=$AZURE_RESOURCE_GROUP" \
    --set "fedcred.oidcIssuerUrl=$oidc_issuer" \
    --set "foundry.endpoint=$AZURE_FOUNDRY_PROJECT_ENDPOINT" \
    --set "foundry.projectEndpoint=$AZURE_FOUNDRY_PROJECT_ENDPOINT" \
    --set-string "foundry.deployments=$AZURE_TEXT_MODEL"

kubectl wait --for=condition=Available deployment/kars-controller \
    --namespace kars-system --timeout=5m
