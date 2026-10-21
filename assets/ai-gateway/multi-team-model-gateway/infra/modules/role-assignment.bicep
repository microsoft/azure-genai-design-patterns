// ILLUSTRATIVE AND UNDEPLOYED - validated with `az bicep build` only.
//
// Grants the APIM managed identity a model-inference role on the shared Foundry account. The role
// definition ID is a parameter that the operator looks up in the tenant (see infra/README.md); it is
// never hard-coded here. Lives in its own module so the assignment name can use the identity's principal
// ID, which is only known after the gateway module has run.

@description('Foundry account that hosts the shared model deployment.')
param foundryAccountName string

@description('Object ID of the principal that receives the role (the APIM system-assigned identity).')
param principalId string

@description('Role definition ID (GUID only), for example Cognitive Services User or Cognitive Services OpenAI User.')
@minLength(36)
@maxLength(36)
param roleDefinitionId string

@description('Principal type of principalId.')
param principalType string = 'ServicePrincipal'

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: foundryAccountName
}

resource modelAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, roleDefinitionId)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
    principalId: principalId
    principalType: principalType
    description: 'Multi-team model gateway: APIM managed identity calls the shared model deployment.'
  }
}

@description('Role assignment resource ID.')
output roleAssignmentId string = modelAccess.id
