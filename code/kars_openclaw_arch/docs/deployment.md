# Deployment

## Prerequisites

Install Azure CLI, AZD, Docker, kubectl, Helm, Kustomize, Python 3.12, and FFmpeg. Replace every angle-bracketed value below with a value from your Azure environment.

The Speech key copied into the original request is compromised and must be regenerated before it is supplied to AZD. Do not use that value.

## Configure

```sh
az login --tenant '<tenant-id>'
az account set --subscription '<subscription-id-or-name>'
azd env new dev
azd env set AZURE_LOCATION '<azure-region>'
azd env set AZURE_RESOURCE_GROUP '<resource-group-name>'
azd env set AKS_CLUSTER_NAME '<aks-cluster-name>'
azd env set AZURE_AKS_ADMIN_PRINCIPAL_ID '<entra-object-id>'
azd env set AZURE_FOUNDRY_PROJECT_ENDPOINT '<foundry-project-endpoint>'
azd env set AZURE_FOUNDRY_ACCOUNT_NAME '<foundry-account-name>'
azd env set AZURE_IMAGE_ENDPOINT '<image-model-endpoint>'
azd env set AZURE_SPEECH_ENDPOINT '<speech-endpoint>'
azd env set AZURE_IMAGE_API_KEY '<current-image-key>'
azd env set AZURE_SPEECH_KEY '<new-speech-key>'
```

Secrets are secure Bicep parameters stored in Key Vault. The post-provision script streams them into the KARS convention secret `media-claw-agent-credentials`; no secret values are stored in project files.

## Validate before deploying

```sh
make validate-local
az bicep build --file infra/main.bicep
azd provision --preview
```

Review the preview for exactly the AKS cluster and region you configured, with no destructive changes to existing resources. Complete the Azure validation workflow before deployment.

## Deploy

```sh
azd up
```

AZD provisions Bicep and deploys the two Container Apps. The post-provision hook obtains AKS credentials, installs pinned KARS `v0.1.25`, builds the Media-Claw image in ACR from `ghcr.io/azure/openclaw-sandbox:v0.1.25`, renders the Azure Kustomize overlay, creates runtime credentials, and waits for rollout.

After deployment:

```sh
azd env get-values
make smoke
kubectl get karssandbox,inferencepolicy,karseval -n finance-media
```

Run `make redteam` only after all sandboxes report Running.
