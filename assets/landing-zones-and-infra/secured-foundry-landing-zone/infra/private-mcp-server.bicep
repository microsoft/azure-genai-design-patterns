// Internal Container App hosting a governed MCP server (Stage 2).
//
// Consumes foundation.bicep outputs. Deploy it on the application lifecycle:
// redeploying this template never touches the registry, vault, workspace, or
// Container Apps environment.
//
// Security posture enforced here:
//   * Mandatory Entra bearer validation (tenant, audience, required scope);
//     development/anonymous authentication is disabled.
//   * ALLOWED_HOSTS is derived from the environment's defaultDomain, so the
//     DNS-rebinding allow-list is correct on the first deployment (no HTTP 421).
//   * Images are pulled with the user-assigned managed identity (AcrPull); no
//     registry admin password.
//   * The Application Insights connection string is a Key Vault secret
//     reference resolved by the managed identity; it never enters a parameter.
//   * Ingress is "external" to the app but the environment itself is internal,
//     so the endpoint is reachable only from linked/peered VNets.
//
// Authorization (which records a principal may see) is the MCP server's job:
// it resolves scope from the validated token principal, never from prompts,
// headers, or card data.

targetScope = 'resourceGroup'

param location string = resourceGroup().location

@description('Container App name, for example "contoso-mcp".')
@minLength(2)
@maxLength(32)
param appName string

@description('foundation.bicep output: environmentId.')
@minLength(1)
param environmentId string

@description('foundation.bicep output: environmentDefaultDomain.')
@minLength(1)
param environmentDefaultDomain string

@description('foundation.bicep output: registryName.')
@minLength(5)
@maxLength(50)
param registryName string

@description('foundation.bicep output: keyVaultName.')
@minLength(1)
param keyVaultName string

@description('foundation.bicep output: appInsightsConnectionSecretUri.')
@minLength(1)
param appInsightsConnectionSecretUri string

@description('Image reference pinned by digest, for example "<registry>.azurecr.io/mcp@sha256:<digest>".')
@minLength(1)
param image string

@description('Entra tenant id that issues accepted tokens.')
@minLength(36)
@maxLength(36)
param tenantId string

@description('Application (client) id of the Entra app registration that exposes the MCP API.')
@minLength(36)
@maxLength(36)
param apiClientId string

@description('Delegated scope a user token must carry.')
param requiredScope string = 'access_as_user'

@description('Optional comma-separated application ids admitted without the delegated scope (for example an agent identity). Each must still resolve to a business scope or be refused.')
param allowedAppIds string = ''

@description('Container port.')
param targetPort int = 8080

param minReplicas int = 1
param maxReplicas int = 3
param tags object = {}

var host = '${appName}.${environmentDefaultDomain}'

var acrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var keyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: registryName
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${appName}-id'
  location: location
  tags: tags
}

resource pullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, identity.id, acrPull)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPull)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource secretsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, identity.id, keyVaultSecretsUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUser)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        // External to the app, reachable only inside the internal environment.
        external: true
        targetPort: targetPort
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: identity.id
        }
      ]
      secrets: [
        {
          name: 'appinsights-connection'
          keyVaultUrl: appInsightsConnectionSecretUri
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'mcp'
          image: image
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'MCP_REQUIRE_AUTH', value: 'true' }
            { name: 'MCP_ALLOW_DEV_AUTH', value: 'false' }
            { name: 'MCP_TENANT_ID', value: tenantId }
            { name: 'MCP_ALLOWED_TENANTS', value: tenantId }
            { name: 'MCP_AUDIENCES', value: '${apiClientId},api://${apiClientId}' }
            { name: 'MCP_REQUIRED_SCOPE', value: requiredScope }
            { name: 'MCP_ALLOWED_APP_IDS', value: allowedAppIds }
            { name: 'MCP_ALLOWED_HOSTS', value: '${host}:*' }
            { name: 'MCP_WRITES_ENABLED', value: 'false' }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights-connection' }
          ]
          probes: [
            { type: 'Liveness', httpGet: { path: '/healthz', port: targetPort }, initialDelaySeconds: 10, periodSeconds: 30 }
            { type: 'Readiness', httpGet: { path: '/readyz', port: targetPort }, initialDelaySeconds: 5, periodSeconds: 10 }
            { type: 'Startup', httpGet: { path: '/healthz', port: targetPort }, periodSeconds: 5, failureThreshold: 24 }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
  dependsOn: [
    pullRole
    secretsRole
  ]
}

output mcpEndpoint string = 'https://${host}/mcp'
output appResourceId string = app.id
output identityPrincipalId string = identity.properties.principalId
output identityClientId string = identity.properties.clientId
