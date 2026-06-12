// Root deployment — creates the resource group's contents:
//   - Log Analytics workspace + Container Apps environment
//   - Storage account with the artifact container
//   - User-assigned managed identity granted Blob Data Contributor + Foundry user
//   - Container App for the webapp (built from ./webapp/Dockerfile)
//
// Foundry project / model deployments are assumed to already exist; supply
// FOUNDRY_PROJECT_ENDPOINT as a parameter so the webapp can call them.

targetScope = 'resourceGroup'

@description('Resource name prefix (lowercase, <= 12 chars).')
@minLength(2)
@maxLength(12)
param namePrefix string = 'skileval'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Foundry project endpoint, e.g. https://<project>.services.ai.azure.com/api/projects/<project>')
param FOUNDRY_PROJECT_ENDPOINT string

@description('Foundry model deployment name for GPT-5.5.')
param MODEL_GPT string = 'gpt-5.5'

@description('Foundry model deployment name for DeepSeek-V4-Pro.')
param MODEL_DEEPSEEK string = 'DeepSeek-V4-Pro'

@description('Foundry agent name for the GPT business agent (optional; leave blank to use raw model).')
param FOUNDRY_AGENT_NAME_GPT string = ''

@description('Foundry agent name for the DeepSeek business agent (optional; leave blank to use raw model).')
param FOUNDRY_AGENT_NAME_DEEPSEEK string = ''

@description('Foundry agent name for the attacker agent.')
param FOUNDRY_AGENT_NAME_ATTACKER string = ''

@description('Foundry agent name for the judge agent.')
param FOUNDRY_AGENT_NAME_JUDGE string = ''

@description('Pinned Foundry agent version for the GPT business agent. Leave blank to use latest.')
param FOUNDRY_AGENT_VERSION_GPT string = ''

@description('Pinned Foundry agent version for the DeepSeek business agent. Leave blank to use latest.')
param FOUNDRY_AGENT_VERSION_DEEPSEEK string = ''

@description('Pinned Foundry agent version for the attacker agent. Leave blank to use latest.')
param FOUNDRY_AGENT_VERSION_ATTACKER string = ''

@description('Pinned Foundry agent version for the judge agent. Leave blank to use latest.')
param FOUNDRY_AGENT_VERSION_JUDGE string = ''

@description('Require business SUT calls to use Foundry Hosted Agents instead of raw model clients.')
param REQUIRE_FOUNDRY_AGENT string = '1'

@description('Container image for the webapp (e.g. <acr>.azurecr.io/skill-eval-webapp:latest). Override after first push.')
param webappImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Optional Azure Container Registry server for image pulls (e.g. myacr.azurecr.io). Leave blank for public images.')
param containerRegistryServer string = ''

var suffix = uniqueString(resourceGroup().id, namePrefix)
var resourceToken = toLower(substring(suffix, 0, 8))

module storage './modules/storage.bicep' = {
  name: 'storage'
  params: {
    namePrefix: namePrefix
    location: location
    resourceToken: resourceToken
  }
}

module identity './modules/identity.bicep' = {
  name: 'identity'
  params: {
    namePrefix: namePrefix
    location: location
    resourceToken: resourceToken
    storageAccountId: storage.outputs.storageAccountId
  }
}

module containerApps './modules/containerapps.bicep' = {
  name: 'containerapps'
  params: {
    namePrefix: namePrefix
    location: location
    resourceToken: resourceToken
    managedIdentityId: identity.outputs.identityId
    managedIdentityClientId: identity.outputs.identityClientId
    webappImage: webappImage
    containerRegistryServer: containerRegistryServer
    envVars: [
      { name: 'FOUNDRY_PROJECT_ENDPOINT', value: FOUNDRY_PROJECT_ENDPOINT }
      { name: 'MODEL_GPT', value: MODEL_GPT }
      { name: 'MODEL_DEEPSEEK', value: MODEL_DEEPSEEK }
      { name: 'FOUNDRY_AGENT_NAME_GPT', value: FOUNDRY_AGENT_NAME_GPT }
      { name: 'FOUNDRY_AGENT_NAME_DEEPSEEK', value: FOUNDRY_AGENT_NAME_DEEPSEEK }
      { name: 'FOUNDRY_AGENT_NAME_ATTACKER', value: FOUNDRY_AGENT_NAME_ATTACKER }
      { name: 'FOUNDRY_AGENT_NAME_JUDGE', value: FOUNDRY_AGENT_NAME_JUDGE }
      { name: 'FOUNDRY_AGENT_VERSION_GPT', value: FOUNDRY_AGENT_VERSION_GPT }
      { name: 'FOUNDRY_AGENT_VERSION_DEEPSEEK', value: FOUNDRY_AGENT_VERSION_DEEPSEEK }
      { name: 'FOUNDRY_AGENT_VERSION_ATTACKER', value: FOUNDRY_AGENT_VERSION_ATTACKER }
      { name: 'FOUNDRY_AGENT_VERSION_JUDGE', value: FOUNDRY_AGENT_VERSION_JUDGE }
      { name: 'REQUIRE_FOUNDRY_AGENT', value: REQUIRE_FOUNDRY_AGENT }
      { name: 'AZURE_STORAGE_ACCOUNT_URL', value: storage.outputs.storageAccountUrl }
      { name: 'AZURE_STORAGE_CONTAINER', value: storage.outputs.containerName }
      { name: 'AZURE_CLIENT_ID', value: identity.outputs.identityClientId }
    ]
  }
}

output webappUrl string = containerApps.outputs.webappUrl
output storageAccountUrl string = storage.outputs.storageAccountUrl
output storageContainer string = storage.outputs.containerName
output managedIdentityClientId string = identity.outputs.identityClientId
output containerRegistryServer string = containerRegistryServer
