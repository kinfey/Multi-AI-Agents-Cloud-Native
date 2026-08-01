#!/bin/sh
set -eu

get_azd_value() {
    azd env get-value "$1"
}

get_keyvault_value() {
    az keyvault secret show \
        --vault-name "$AZURE_KEY_VAULT_NAME" \
        --name "$1" --query value -o tsv
}

: "${AZURE_SUBSCRIPTION_ID:?Set AZURE_SUBSCRIPTION_ID to your Azure subscription ID}"
: "${AZURE_TENANT_ID:?Set AZURE_TENANT_ID to your Microsoft Entra tenant ID}"
: "${AZURE_RESOURCE_GROUP:?Set AZURE_RESOURCE_GROUP to your resource group name}"
export AKS_CLUSTER_NAME=${AKS_CLUSTER_NAME:-$(get_azd_value AKS_CLUSTER_NAME)}
export AZURE_CONTAINER_REGISTRY_ENDPOINT=${AZURE_CONTAINER_REGISTRY_ENDPOINT:-$(get_azd_value AZURE_CONTAINER_REGISTRY_ENDPOINT)}
export AZURE_STORAGE_ACCOUNT_URL=${AZURE_STORAGE_ACCOUNT_URL:-$(get_azd_value AZURE_STORAGE_ACCOUNT_URL)}
export AZURE_KEY_VAULT_NAME=${AZURE_KEY_VAULT_NAME:-$(get_azd_value AZURE_KEY_VAULT_NAME)}
export AKS_WORKLOAD_IDENTITY_CLIENT_ID=${AKS_WORKLOAD_IDENTITY_CLIENT_ID:-$(get_azd_value AKS_WORKLOAD_IDENTITY_CLIENT_ID)}
export AZURE_FOUNDRY_PROJECT_ENDPOINT=${AZURE_FOUNDRY_PROJECT_ENDPOINT:-$(get_azd_value AZURE_FOUNDRY_PROJECT_ENDPOINT)}
export AZURE_IMAGE_ENDPOINT=${AZURE_IMAGE_ENDPOINT:-$(get_azd_value AZURE_IMAGE_ENDPOINT)}
export AZURE_TEXT_MODEL=${AZURE_TEXT_MODEL:-gpt-5.5}
export AZURE_IMAGE_MODEL=${AZURE_IMAGE_MODEL:-MAI-Image-2.5-Pro}
export AZURE_SPEECH_ENDPOINT=$(get_keyvault_value azure-speech-endpoint)
export AZURE_SPEECH_VOICE=$(get_keyvault_value azure-speech-voice)
export TTS_BACKEND=$(get_keyvault_value tts-backend)
export AZURE_VOICE_MODEL=$(get_keyvault_value azure-voice-model)
export AZURE_VOICE_NAME_CN=$(get_keyvault_value azure-voice-name-cn)
export AZURE_VOICE_NAME_EN=$(get_keyvault_value azure-voice-name-en)
export NEWS_FEED_URLS=${NEWS_FEED_URLS:-https://feeds.reuters.com/reuters/businessNews}

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
az aks get-credentials \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$AKS_CLUSTER_NAME" \
    --overwrite-existing \
    --output none

"$(dirname "$0")/install-kars.sh"

registry_name=${AZURE_CONTAINER_REGISTRY_ENDPOINT%%.*}
image_tag=${AZURE_ENV_NAME:-dev}
az acr build \
    --registry "$registry_name" \
    --image "media-claw-agent:$image_tag" \
    --file agents/media-claw-agent/Dockerfile .

export MEDIA_CLAW_OPENCLAW_IMAGE="$AZURE_CONTAINER_REGISTRY_ENDPOINT/media-claw-agent:$image_tag"
export KARS_OPENCLAW_IMAGE=${KARS_OPENCLAW_IMAGE:-ghcr.io/azure/openclaw-sandbox:v0.1.25}
kubectl kustomize kars/overlays/azure | envsubst | kubectl apply -f -

kubectl wait --for=jsonpath='{.status.phase}'=Running \
    karssandbox/media-claw-agent \
    --namespace finance-media \
    --timeout=10m

image_key=$(az keyvault secret show \
    --vault-name "$AZURE_KEY_VAULT_NAME" \
    --name mai-image-api-key --query value -o tsv)
speech_key=$(az keyvault secret show \
    --vault-name "$AZURE_KEY_VAULT_NAME" \
    --name azure-speech-key --query value -o tsv)

kubectl create secret generic media-claw-agent-credentials \
    --namespace kars-media-claw-agent \
    --from-literal="AZURE_CLIENT_ID=$AKS_WORKLOAD_IDENTITY_CLIENT_ID" \
    --from-literal="AZURE_STORAGE_ACCOUNT_URL=$AZURE_STORAGE_ACCOUNT_URL" \
    --from-literal="AZURE_STORAGE_CONTAINER=media" \
    --from-literal="AZURE_FOUNDRY_PROJECT_ENDPOINT=$AZURE_FOUNDRY_PROJECT_ENDPOINT" \
    --from-literal="AZURE_TEXT_MODEL=$AZURE_TEXT_MODEL" \
    --from-literal="AZURE_IMAGE_ENDPOINT=$AZURE_IMAGE_ENDPOINT" \
    --from-literal="AZURE_IMAGE_MODEL=$AZURE_IMAGE_MODEL" \
    --from-literal="AZURE_IMAGE_API_KEY=$image_key" \
    --from-literal="AZURE_SPEECH_ENDPOINT=$AZURE_SPEECH_ENDPOINT" \
    --from-literal="AZURE_SPEECH_KEY=$speech_key" \
    --from-literal="AZURE_SPEECH_VOICE=$AZURE_SPEECH_VOICE" \
    --from-literal="TTS_BACKEND=$TTS_BACKEND" \
    --from-literal="AZURE_VOICE_MODEL=$AZURE_VOICE_MODEL" \
    --from-literal="AZURE_VOICE_NAME_CN=$AZURE_VOICE_NAME_CN" \
    --from-literal="AZURE_VOICE_NAME_EN=$AZURE_VOICE_NAME_EN" \
    --from-literal="NEWS_FEED_URLS=$NEWS_FEED_URLS" \
    --dry-run=client -o yaml | kubectl apply -f -

unset image_key speech_key
kubectl rollout restart deployment/media-claw-agent --namespace kars-media-claw-agent
kubectl rollout status deployment/media-claw-agent --namespace kars-media-claw-agent --timeout=10m
