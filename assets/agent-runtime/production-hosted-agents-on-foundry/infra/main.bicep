// ILLUSTRATIVE AND UNDEPLOYED.
//
// Production Hosted Agents on Microsoft Foundry: the runtime-layer additions that sit
// on top of the Secured Foundry Landing Zone. This template compiles with `bicep build`
// but has not been deployed as-is. Hosted agents are a preview capability: verify API
// versions, built-in role names, role definition ids and which identity Foundry uses for
// each step against current Microsoft Learn documentation before you adapt it.
//
// What it does:
//   - references existing landing zone resources: Foundry account and project, the
//     private container registry, the Cosmos DB account and Application Insights
//   - creates a user-assigned managed identity for the release pipeline, with an
//     optional federated credential for the CI system
//   - creates a Cosmos DB database and containers for threads, checkpoints,
//     idempotency records and the session registry used by the orphan sweeper
//   - assigns least-privilege data-plane roles to the release identity, the image
//     pull identity, the agent runtime identity and an optional caller application
//
// What it does not do:
//   - networking, private endpoints, private DNS or the runner VM (the landing zone
//     owns all of these)
//   - agent versions or sessions (data-plane objects that the pipeline creates from
//     inside the VNet, not Azure Resource Manager resources)
//   - operator access (grant it just in time through Privileged Identity Management)

targetScope = 'resourceGroup'

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

@description('Azure region for new resources. Defaults to the resource group location.')
param location string = resourceGroup().location

@description('Short prefix for new resource names, for example contoso-agents.')
@maxLength(20)
param namePrefix string = 'contoso-agents'

@description('Name of the existing Foundry account created by the landing zone.')
param foundryAccountName string

@description('Name of the existing Foundry project that hosts the agent.')
param foundryProjectName string

@description('Name of the existing private container registry created by the landing zone.')
param containerRegistryName string

@description('Name of the existing Cosmos DB account created by the landing zone. Local (key) authentication must already be disabled.')
param cosmosAccountName string

@description('Name of the existing Application Insights component created by the landing zone.')
param appInsightsName string

@description('Cosmos DB database for agent state. This template creates it.')
param cosmosDatabaseName string = 'hosted-agent-state'

@description('Default time-to-live, in seconds, for idempotency records. Set it from your retention policy; it is a policy value, not a performance setting.')
param idempotencyTtlSeconds int

@description('Principal (object) id of the hosted agent runtime identity: the instance identity reported for the agent version, or a customer-managed identity where supported. Leave empty on the first run, because the instance identity exists only after the first version is created (observed in preview; verify).')
param runtimePrincipalId string = ''

@description('Principal id of the identity Foundry uses to pull images from the private registry. Current docs point to the project managed identity; verify for your build.')
param imagePullPrincipalId string

@description('Optional principal id of a caller application, for example the web tier managed identity, that invokes the agent.')
param callerPrincipalId string = ''

@description('Optional OIDC issuer for a federated credential on the release identity. Leave empty to skip federation and attach the identity to the in-network runner instead.')
param federatedIssuer string = ''

@description('Optional federated credential subject, for example repo:contoso/agents:environment:prod.')
param federatedSubject string = ''

// Role definition ids. Only ids that are well-known public built-ins carry defaults.
// Everything else is required, so the template never guesses an id. Look the ids up
// in the Azure built-in roles reference and verify the names are still current.

@description('Built-in role definition id for Foundry User (formerly named Azure AI User). Verify against current docs.')
param roleIdFoundryUser string = '53ca6127-db72-4b80-b1b0-d745d6d5456d'

@description('Built-in role definition id for AcrPull.')
param roleIdAcrPull string = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

@description('Built-in role definition id for AcrPush. Look it up in the built-in roles reference.')
param roleIdAcrPush string

@description('Built-in role definition id for Cognitive Services OpenAI User. Look it up in the built-in roles reference.')
param roleIdOpenAiUser string

@description('Built-in role definition id for Monitoring Metrics Publisher. Leave empty unless telemetry ingestion uses Entra authentication.')
param roleIdMetricsPublisher string = ''

@description('Cosmos DB SQL role definition id. 00000000-0000-0000-0000-000000000002 is the Cosmos DB Built-in Data Contributor.')
param cosmosDataRoleId string = '00000000-0000-0000-0000-000000000002'

// ---------------------------------------------------------------------------
// Existing landing zone resources
// ---------------------------------------------------------------------------

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: foundryAccountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: account
  name: foundryProjectName
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: containerRegistryName
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosAccountName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

// ---------------------------------------------------------------------------
// Release identity
// ---------------------------------------------------------------------------

resource releaseIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${namePrefix}-release'
  location: location
}

resource releaseFederation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = if (!empty(federatedIssuer) && !empty(federatedSubject)) {
  parent: releaseIdentity
  name: 'ci-${uniqueString(federatedIssuer, federatedSubject)}'
  properties: {
    issuer: federatedIssuer
    subject: federatedSubject
    audiences: [
      'api://AzureADTokenExchange'
    ]
  }
}

// ---------------------------------------------------------------------------
// Agent state in Cosmos DB
// ---------------------------------------------------------------------------

// defaultTtl -1 turns TTL on without a default, so individual items can carry a ttl.
var stateContainers = [
  {
    name: 'threads'
    partitionKey: '/conversation_key'
    ttl: -1
  }
  {
    name: 'checkpoints'
    partitionKey: '/conversation_key'
    ttl: -1
  }
  {
    name: 'idempotency'
    partitionKey: '/conversation_key'
    ttl: idempotencyTtlSeconds
  }
  {
    name: 'sessions'
    partitionKey: '/agent_name'
    ttl: -1
  }
]

resource stateDb 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: cosmos
  name: cosmosDatabaseName
  properties: {
    resource: {
      id: cosmosDatabaseName
    }
  }
}

resource stateContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = [
  for c in stateContainers: {
    parent: stateDb
    name: c.name
    properties: {
      resource: {
        id: c.name
        partitionKey: {
          paths: [
            c.partitionKey
          ]
          kind: 'Hash'
          version: 2
        }
        defaultTtl: c.ttl
      }
    }
  }
]

// ---------------------------------------------------------------------------
// Role assignments
// ---------------------------------------------------------------------------

var runtimeEnabled = !empty(runtimePrincipalId)

// Release identity: push images by digest. No pull, no delete, no registry admin.
resource releaseAcrPush 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, releaseIdentity.id, roleIdAcrPush)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIdAcrPush)
    principalId: releaseIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Release identity: create agent versions, run smoke turns, switch the active version.
// Project scope only; no account-level or control-plane role.
resource releaseProjectUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: project
  name: guid(project.id, releaseIdentity.id, roleIdFoundryUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIdFoundryUser)
    principalId: releaseIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Image pull identity: pull the agent image into session sandboxes.
resource imagePullAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, imagePullPrincipalId, roleIdAcrPull)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIdAcrPull)
    principalId: imagePullPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Runtime identity: call model deployments on the Foundry account.
resource runtimeOpenAiUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (runtimeEnabled) {
  scope: account
  name: guid(account.id, runtimePrincipalId, roleIdOpenAiUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIdOpenAiUser)
    principalId: runtimePrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Runtime identity: read and write agent state. Cosmos DB data-plane RBAC, scoped to
// this database only, not the whole account.
resource runtimeCosmosData 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = if (runtimeEnabled) {
  parent: cosmos
  name: guid(cosmos.id, runtimePrincipalId, cosmosDataRoleId, cosmosDatabaseName)
  properties: {
    principalId: runtimePrincipalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataRoleId}'
    scope: '${cosmos.id}/dbs/${cosmosDatabaseName}'
  }
  dependsOn: [
    stateDb
  ]
}

// Runtime identity: publish telemetry when ingestion uses Entra authentication.
resource runtimeMetricsPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (runtimeEnabled && !empty(roleIdMetricsPublisher)) {
  scope: appInsights
  name: guid(appInsights.id, runtimePrincipalId, roleIdMetricsPublisher)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIdMetricsPublisher)
    principalId: runtimePrincipalId
    principalType: 'ServicePrincipal'
  }
}

// Caller application: invoke the agent through the private endpoint.
resource callerProjectUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(callerPrincipalId)) {
  scope: project
  name: guid(project.id, callerPrincipalId, roleIdFoundryUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleIdFoundryUser)
    principalId: callerPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output releaseIdentityResourceId string = releaseIdentity.id
output releaseIdentityClientId string = releaseIdentity.properties.clientId
output releaseIdentityPrincipalId string = releaseIdentity.properties.principalId
output stateDatabaseName string = stateDb.name
output stateContainerNames array = [for c in stateContainers: c.name]
output projectEndpoint string = 'https://${account.name}.services.ai.azure.com/api/projects/${project.name}'
output registryLoginServer string = registry.properties.loginServer
output appInsightsResourceId string = appInsights.id
output runtimeRolesAssigned bool = runtimeEnabled
