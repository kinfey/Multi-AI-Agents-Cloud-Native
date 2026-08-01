targetScope = 'resourceGroup'

@minLength(1)
@maxLength(32)
@description('AZD environment name used for deterministic resource naming.')
param environmentName string

@description('Primary deployment location.')
param location string = resourceGroup().location

@description('Stable AKS Kubernetes version returned by az aks get-versions for the deployment region.')
param kubernetesVersion string = '1.35'

@description('Microsoft Entra object ID granted AKS data-plane cluster administrator access for deployment.')
param aksAdminPrincipalId string

@description('AKS cluster name supplied by the deployer.')
param aksClusterName string

@secure()
@description('MAI Image API key. Supply through an AZD environment variable; never commit a value.')
param imageApiKey string

@secure()
@description('Azure Speech key. Rotate the key exposed in the original request before provisioning.')
param speechKey string

@description('Existing Microsoft Foundry project endpoint.')
param foundryProjectEndpoint string

@description('Existing Microsoft Foundry account name.')
param foundryAccountName string

@description('Existing MAI Image endpoint.')
param imageEndpoint string

@description('Existing Azure Speech endpoint.')
param speechEndpoint string

@description('Chinese Speech speaker configured for the media agent.')
param speechVoice string = 'zh-CN-Mei'

@description('TTS provider selected by the media agent.')
param ttsBackend string = 'mai-voice'

@description('MAI voice model suffix used by the Speech SDK.')
param voiceModel string = 'MAI-Voice-2'

@description('Chinese MAI Voice speaker.')
param voiceNameCn string = 'zh-CN-Mei'

@description('English MAI Voice speaker.')
param voiceNameEn string = 'en-US-Olivia'

var resourceToken = uniqueString(subscription().id, resourceGroup().id, location, environmentName)
var tags = {
  environment: environmentName
  workload: 'finance-shortvideo'
  'managed-by': 'azd'
}

module shared 'modules/shared.bicep' = {
  params: {
    location: location
    resourceToken: resourceToken
    tags: tags
    imageApiKey: imageApiKey
    speechKey: speechKey
    speechEndpoint: speechEndpoint
    speechVoice: speechVoice
    ttsBackend: ttsBackend
    voiceModel: voiceModel
    voiceNameCn: voiceNameCn
    voiceNameEn: voiceNameEn
  }
}

module aks 'modules/aks.bicep' = {
  params: {
    location: location
    resourceToken: resourceToken
    tags: tags
    kubernetesVersion: kubernetesVersion
    aksAdminPrincipalId: aksAdminPrincipalId
    clusterName: aksClusterName
    containerRegistryId: shared.outputs.containerRegistryId
    storageAccountId: shared.outputs.storageAccountId
    keyVaultId: shared.outputs.keyVaultId
    logAnalyticsWorkspaceId: shared.outputs.logAnalyticsWorkspaceId
    workloadIdentityName: shared.outputs.workloadIdentityName
    workloadIdentityPrincipalId: shared.outputs.workloadIdentityPrincipalId
    foundryAccountName: foundryAccountName
  }
}

module containerApps 'modules/container-apps.bicep' = {
  params: {
    location: location
    resourceToken: resourceToken
    tags: tags
    containerRegistryId: shared.outputs.containerRegistryId
    containerRegistryEndpoint: shared.outputs.containerRegistryEndpoint
    storageAccountId: shared.outputs.storageAccountId
    storageAccountUrl: shared.outputs.storageAccountUrl
    logAnalyticsWorkspaceName: shared.outputs.logAnalyticsWorkspaceName
    applicationInsightsConnectionString: shared.outputs.applicationInsightsConnectionString
    appIdentityId: shared.outputs.appIdentityId
    appIdentityClientId: shared.outputs.appIdentityClientId
    appIdentityPrincipalId: shared.outputs.appIdentityPrincipalId
  }
}

output RESOURCE_GROUP_ID string = resourceGroup().id
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = shared.outputs.containerRegistryEndpoint
output AZURE_STORAGE_ACCOUNT_URL string = shared.outputs.storageAccountUrl
output AZURE_STORAGE_CONTAINER string = 'media'
output AZURE_REPORT_CONTAINER string = 'redteam'
output AZURE_KEY_VAULT_NAME string = shared.outputs.keyVaultName
output AZURE_CLIENT_ID string = shared.outputs.appIdentityClientId
output AKS_CLUSTER_NAME string = aks.outputs.clusterName
output AKS_WORKLOAD_IDENTITY_CLIENT_ID string = shared.outputs.workloadIdentityClientId
output MEDIA_APP_URI string = containerApps.outputs.mediaAppUri
output REDTEAM_APP_URI string = containerApps.outputs.redteamAppUri
output AZURE_CONTAINER_APPS_ENVIRONMENT_NAME string = containerApps.outputs.environmentName
output SERVICE_MEDIA_APP_RESOURCE_NAME string = containerApps.outputs.mediaAppName
output SERVICE_REDTEAM_APP_RESOURCE_NAME string = containerApps.outputs.redteamAppName
output AZURE_FOUNDRY_PROJECT_ENDPOINT string = foundryProjectEndpoint
output AZURE_IMAGE_ENDPOINT string = imageEndpoint
output AZURE_SPEECH_ENDPOINT string = speechEndpoint
