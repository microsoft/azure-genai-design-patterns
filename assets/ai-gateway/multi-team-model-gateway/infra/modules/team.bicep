// ILLUSTRATIVE AND UNDEPLOYED - validated with `az bicep build` only.
//
// Everything one team needs to call the shared model deployment through the gateway:
//   - APIM product team-<name> with the per-team token limit (counter key = product name),
//   - exactly one subscription sub-team-<name> scoped to that product (the team's gateway key),
//   - Foundry project team-<name> under the shared account,
//   - ApiManagement connection ai-gateway-team-<name> in that project carrying the subscription key
//     (isSharedToAll=false; the name carries the team suffix because connection names must be unique
//     across every project in the account),
//   - Foundry User role assignments for the team's members, scoped to this project only.

@description('Azure region for the project.')
param location string

@description('Short lowercase team name.')
param teamName string

@description('Token-per-minute budget for this team.')
@minValue(1)
param tokensPerMinute int

@description('Entra object IDs that receive Foundry User on this project.')
param memberPrincipalIds string[] = []

@description('Principal type of memberPrincipalIds.')
@allowed([
  'Group'
  'User'
  'ServicePrincipal'
])
param memberPrincipalType string = 'Group'

@description('API Management service name.')
param apimName string

@description('API Management resource ID, recorded in the connection metadata.')
param apimId string

@description('Name of the shared model API.')
param apiName string

@description('Azure OpenAI-compatible endpoint that the connection targets (<gatewayUrl>/<apiPathPrefix>).')
param gatewayTarget string

@description('Foundry account that hosts the team projects.')
param foundryAccountName string

@description('Role definition ID (GUID only) of the Foundry User built-in role.')
@minLength(36)
@maxLength(36)
param foundryUserRoleDefinitionId string

@description('Tags.')
param tags object = {}

var productName = 'team-${teamName}'
var subscriptionName = 'sub-team-${teamName}'
var projectName = 'team-${teamName}'
var connectionName = 'ai-gateway-team-${teamName}'

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: apimName
}

resource api 'Microsoft.ApiManagement/service/apis@2024-05-01' existing = {
  parent: apim
  name: apiName
}

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: foundryAccountName
}

// displayName equals the resource name on purpose: the token-limit counter key is context.Product.Name.
resource product 'Microsoft.ApiManagement/service/products@2024-05-01' = {
  parent: apim
  name: productName
  properties: {
    displayName: productName
    description: 'Team ${teamName}: per-team token budget and chargeback counter for the shared model deployment.'
    subscriptionRequired: true
    approvalRequired: true
    subscriptionsLimit: 1
    state: 'published'
  }
}

resource productApi 'Microsoft.ApiManagement/service/products/apis@2024-05-01' = {
  parent: product
  name: api.name
}

resource productPolicy 'Microsoft.ApiManagement/service/products/policies@2024-05-01' = {
  parent: product
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: replace(loadTextContent('../policies/team-token-limit.xml'), '__TOKENS_PER_MINUTE__', string(tokensPerMinute))
  }
}

resource subscription 'Microsoft.ApiManagement/service/subscriptions@2024-05-01' = {
  parent: apim
  name: subscriptionName
  properties: {
    displayName: 'Team ${teamName} gateway key'
    scope: product.id
    state: 'active'
    allowTracing: false
  }
  dependsOn: [
    productApi
    productPolicy
  ]
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: account
  name: projectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  tags: tags
  properties: {
    displayName: 'Team ${teamName}'
    description: 'Team ${teamName} project. Calls the shared model deployment through the gateway connection ${connectionName}.'
  }
}

resource gatewayConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: connectionName
  properties: {
    category: 'ApiManagement'
    target: gatewayTarget
    authType: 'ApiKey'
    isSharedToAll: false
    credentials: {
      key: subscription.listSecrets().primaryKey
    }
    metadata: {
      ResourceId: apimId
      type: 'azure_api_management'
    }
  }
}

resource memberAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for principalId in memberPrincipalIds: {
    name: guid(project.id, principalId, foundryUserRoleDefinitionId)
    scope: project
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUserRoleDefinitionId)
      principalId: principalId
      principalType: memberPrincipalType
      description: 'Multi-team model gateway: team ${teamName} members use their own project only.'
    }
  }
]

@description('APIM product name (also the token-limit counter key).')
output productName string = product.name

@description('APIM subscription name. The key itself is never output.')
output subscriptionName string = subscription.name

@description('Foundry project name.')
output projectName string = project.name

@description('Foundry project endpoint used by AIProjectClient.')
output projectEndpoint string = 'https://${account.properties.customSubDomainName}.services.ai.azure.com/api/projects/${project.name}'

@description('Project connection that carries the team gateway key.')
output connectionName string = gatewayConnection.name
