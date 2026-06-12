@description('Resource name prefix.')
param namePrefix string

@description('Region.')
param location string

@description('Short, deterministic token derived from RG id.')
param resourceToken string

@description('Resource ID of the storage account that should be writable by the identity.')
param storageAccountId string

var identityName = '${namePrefix}-mi-${resourceToken}'

// Built-in role definition IDs.
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var azureAiUserRoleId = '53ca6127-db72-4b80-b1b0-d745d6d5456d'   // "Azure AI User"

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

// Reference existing storage account (created in storage module).
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: last(split(storageAccountId, '/'))
}

resource storageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, identity.id, storageBlobDataContributorRoleId)
  scope: storage
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
  }
}

// Resource-group scoped role assignment for Foundry "Azure AI User".
// If the Foundry project lives in a different resource group, grant the same
// role at that Foundry project/resource-group scope after deployment.
resource foundryRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, identity.id, azureAiUserRoleId)
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureAiUserRoleId)
  }
}

output identityId string = identity.id
output identityClientId string = identity.properties.clientId
output identityPrincipalId string = identity.properties.principalId
