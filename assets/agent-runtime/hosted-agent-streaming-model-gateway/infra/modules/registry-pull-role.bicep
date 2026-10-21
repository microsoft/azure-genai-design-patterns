// ILLUSTRATIVE AND UNDEPLOYED.
//
// Grants the gateway identity AcrPull on one existing container registry and
// returns its login server. Deployed by main.bicep at the resource group that
// holds the registry, which may differ from the gateway's resource group.
//
// What it does:
//   * References the registry by name. It never creates or modifies it.
//   * Assigns AcrPull at the registry scope only.
//
// What it does not do:
//   * Push images or grant push rights. The build pipeline has its own identity.

targetScope = 'resourceGroup'

@description('Name of the existing private container registry that holds the gateway image.')
param registryName string

@description('Principal (object) ID of the gateway user-assigned managed identity.')
param principalId string

@description('Role definition ID of AcrPull (well-known built-in role).')
param acrPullRoleDefinitionId string

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}

resource pullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, principalId, acrPullRoleDefinitionId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleDefinitionId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output loginServer string = registry.properties.loginServer
