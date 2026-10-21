// ILLUSTRATIVE AND UNDEPLOYED.
//
// Grants the gateway identity a model inference role on one existing Foundry
// account, and returns the OpenAI-compatible base URL the gateway calls.
// Deployed by main.bicep at the resource group that holds the account, which
// may differ from the gateway's resource group.
//
// What it does:
//   * References the Foundry account by name. It never creates or modifies it.
//   * Assigns the caller-supplied role at the account scope only.
//   * Reports whether local (key) authentication is disabled so the pipeline
//     can stop when it is not.
//
// What it does not do:
//   * Choose the role. The role definition ID is a required input.
//   * Create model deployments. The aliases in the gateway policy must map to
//     deployments that already exist.

targetScope = 'resourceGroup'

@description('Name of the existing Foundry (AI Services) account the gateway calls.')
param foundryAccountName string

@description('Principal (object) ID of the gateway user-assigned managed identity.')
param principalId string

@description('Role definition ID (GUID) of the model inference role. Required; the template never guesses an ID.')
param roleDefinitionId string

resource foundry 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: foundryAccountName
}

resource modelRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, principalId, roleDefinitionId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

// OpenAI-compatible v1 surface of the account. The gateway appends
// /chat/completions and sends the deployment name as the model. Verify the
// host name form against current Foundry documentation for your account kind.
output openAiV1BaseUrl string = 'https://${foundry.properties.customSubDomainName}.openai.azure.com/openai/v1'

output localAuthDisabled bool = foundry.properties.?disableLocalAuth ?? false

output accountId string = foundry.id
