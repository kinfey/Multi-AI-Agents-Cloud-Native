#!/bin/sh
set -eu

: "${AZURE_SUBSCRIPTION_ID:?Set AZURE_SUBSCRIPTION_ID to your Azure subscription ID}"
: "${AZURE_RESOURCE_GROUP:?Set AZURE_RESOURCE_GROUP to your resource group name}"
: "${AZURE_SPEECH_ACCOUNT_NAME:?Set AZURE_SPEECH_ACCOUNT_NAME to your Speech account name}"

subscription=$AZURE_SUBSCRIPTION_ID
resource_group=$AZURE_RESOURCE_GROUP
account_name=$AZURE_SPEECH_ACCOUNT_NAME
key_name=${AZURE_SPEECH_KEY_NAME:-key1}

az account set --subscription "$subscription"
az cognitiveservices account keys regenerate \
    --resource-group "$resource_group" \
    --name "$account_name" \
    --key-name "$key_name" \
    --output none

speech_key=$(az cognitiveservices account keys list \
    --resource-group "$resource_group" \
    --name "$account_name" \
    --query "$key_name" \
    --output tsv)
azd env set AZURE_SPEECH_KEY "$speech_key"
unset speech_key

echo "Speech $key_name was rotated and the active AZD environment was updated."
