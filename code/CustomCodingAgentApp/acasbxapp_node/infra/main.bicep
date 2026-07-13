targetScope = 'resourceGroup'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Normalized resource prefix. Use azure-openclaw-aca for the requested azure openclaw_aca name.')
@minLength(5)
param prefix string = 'azure-openclaw-aca'

@description('Globally unique Azure Container Registry name. Lowercase letters and numbers only.')
@minLength(5)
@maxLength(50)
param acrName string = 'azureopenclawaca'

@description('Globally unique storage account name. Lowercase letters and numbers only.')
@minLength(3)
@maxLength(24)
param storageName string = 'stazureopenclawaca'

@description('Container image to deploy, for example <acr>.azurecr.io/openclaw:latest.')
param openclawImage string

@description('Microsoft Foundry or Azure AI Foundry endpoint used by OpenClaw.')
param foundryEndpoint string

@description('Microsoft Foundry model deployment name. The requested value is gpt-5.5.')
param foundryModelDeployment string = 'gpt-5.5'

@secure()
@description('OpenClaw gateway token. Generate with openssl rand -hex 24.')
param openclawGatewayToken string

@description('Existing ACA sandbox id used by the custom OpenClaw ACA backend.')
param acaSandboxId string

var cleanPrefix = replace(toLower(prefix), '_', '-')
var environmentName = '${cleanPrefix}-env'
var appName = '${cleanPrefix}-app'
var acrPullIdentityName = '${cleanPrefix}-acr-pull-mi'
var fileShareName = 'openclaw'
var envStorageName = 'openclaw-fileshare'
var acrPullRoleDefinitionId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var acrPushRoleDefinitionId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8311e382-0749-4cb8-b61a-304f252e45ec')
var acrTasksContributorRoleDefinitionId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'fb382eab-e894-4461-af04-94435c366c3f')
var containerAppsContributorRoleDefinitionId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '358470bc-b998-42bd-ab17-a7e34c199c0f')

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

resource acrPullIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: acrPullIdentityName
  location: location
}

resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, acrPullIdentity.id, 'acr-pull')
  scope: acr
  properties: {
    principalId: acrPullIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleDefinitionId
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource fileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: fileShareName
  properties: {
    shareQuota: 32
    enabledProtocols: 'SMB'
  }
}

resource containerAppsEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {}
}

resource envStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: containerAppsEnv
  name: envStorageName
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: fileShare.name
      accessMode: 'ReadWrite'
    }
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'SystemAssigned, UserAssigned'
    userAssignedIdentities: {
      '${acrPullIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 18789
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: acrPullIdentity.id
        }
      ]
      secrets: [
        {
          name: 'openclaw-gateway-token'
          value: openclawGatewayToken
        }
      ]
    }
    template: {
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
      containers: [
        {
          name: 'openclaw'
          image: openclawImage
          env: [
            {
              name: 'OPENCLAW_GATEWAY_TOKEN'
              secretRef: 'openclaw-gateway-token'
            }
            {
              name: 'OPENCLAW_CONFIG_PATH'
              value: '/state/openclaw/openclaw.json5'
            }
            {
              name: 'AZURE_AI_FOUNDRY_ENDPOINT'
              value: foundryEndpoint
            }
            {
              name: 'AZURE_AI_FOUNDRY_MODEL_DEPLOYMENT'
              value: foundryModelDeployment
            }
            {
              name: 'NODE_OPTIONS'
              value: '--max-old-space-size=1024'
            }
            {
              name: 'AZURE_RESOURCE_GROUP'
              value: resourceGroup().name
            }
            {
              name: 'AZURE_SUBSCRIPTION_ID'
              value: subscription().subscriptionId
            }
            {
              name: 'ACA_SUBSCRIPTION'
              value: subscription().subscriptionId
            }
            {
              name: 'AZURE_LOCATION'
              value: location
            }
            {
              name: 'ACR_NAME'
              value: acr.name
            }
            {
              name: 'CONTAINER_APP_ENVIRONMENT'
              value: containerAppsEnv.name
            }
            {
              name: 'OPENCLAW_DEPLOY_IDENTITY_ID'
              value: acrPullIdentity.id
            }
            {
              name: 'OPENCLAW_ACA_SANDBOX_GROUP'
              value: '${cleanPrefix}-sandbox'
            }
            {
              name: 'OPENCLAW_ACA_SANDBOX_ID'
              value: acaSandboxId
            }
          ]
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          volumeMounts: [
            {
              volumeName: 'openclaw-state'
              mountPath: '/state/openclaw'
            }
          ]
        }
      ]
      volumes: [
        {
          name: 'openclaw-state'
          storageType: 'AzureFile'
          storageName: envStorage.name
        }
      ]
    }
  }
  dependsOn: [
    acrPullAssignment
  ]
}

resource deploymentContainerAppsAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, containerApp.id, 'container-apps-contributor')
  properties: {
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: containerAppsContributorRoleDefinitionId
  }
}

resource deploymentAcrPushAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, containerApp.id, 'acr-push')
  scope: acr
  properties: {
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPushRoleDefinitionId
  }
}

resource deploymentAcrTasksAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, containerApp.id, 'acr-tasks-contributor')
  scope: acr
  properties: {
    principalId: containerApp.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrTasksContributorRoleDefinitionId
  }
}

output acrLoginServer string = acr.properties.loginServer
output containerAppName string = containerApp.name
output containerAppPrincipalId string = containerApp.identity.principalId
output acrPullIdentityClientId string = acrPullIdentity.properties.clientId
output containerAppsEnvironmentName string = containerAppsEnv.name
output storageAccountName string = storage.name
output fileShareName string = fileShare.name
output gatewayUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
