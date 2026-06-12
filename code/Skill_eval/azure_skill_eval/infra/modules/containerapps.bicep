@description('Resource name prefix.')
param namePrefix string

@description('Region.')
param location string

@description('Short, deterministic token derived from RG id.')
param resourceToken string

@description('Resource ID of the user-assigned managed identity.')
param managedIdentityId string

@description('Client ID of the managed identity (passed in as AZURE_CLIENT_ID).')
param managedIdentityClientId string

@description('Container image reference for the webapp.')
param webappImage string

@description('Optional container registry server (e.g. myacr.azurecr.io). Blank for public images.')
param containerRegistryServer string = ''

@description('Environment variables passed to the webapp container.')
param envVars array

var workspaceName = '${namePrefix}-log-${resourceToken}'
var environmentName = '${namePrefix}-cae-${resourceToken}'
var webappName = '${namePrefix}-web-${resourceToken}'

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: workspaceName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: listKeys(logAnalytics.id, '2022-10-01').primarySharedKey
      }
    }
  }
}

var registryConfig = empty(containerRegistryServer) ? [] : [
  {
    server: containerRegistryServer
    identity: managedIdentityId
  }
]

resource webapp 'Microsoft.App/containerApps@2024-03-01' = {
  name: webappName
  location: location
  tags: {
    'azd-service-name': 'webapp'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityId}': {}
    }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: registryConfig
    }
    template: {
      containers: [
        {
          name: 'webapp'
          image: webappImage
          resources: {
            cpu: json('1.0')
            memory: '2.0Gi'
          }
          env: union(envVars, [
            { name: 'AZURE_CLIENT_ID', value: managedIdentityClientId }
          ])
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

output webappUrl string = 'https://${webapp.properties.configuration.ingress.fqdn}'
output webappName string = webapp.name
output environmentName string = environment.name
