// ILLUSTRATIVE AND UNDEPLOYED - validated with `az bicep build` only. Deploy to a sandbox subscription first.
//
// Multi-Team Model Gateway
// One shared Microsoft Foundry (AIServices) model deployment fronted by Azure API Management acting as an
// AI gateway. Every team gets:
//   - an APIM product (the token-limit counter key) and exactly one subscription (the team's gateway key),
//   - its own Foundry project with project-scoped Foundry User access for the team's members,
//   - a non-shared ApiManagement connection in that project that carries the team's gateway key.
// Teams never receive a model key: the gateway reaches the model endpoint with its managed identity.
targetScope = 'resourceGroup'

@description('One team that shares the model deployment through the gateway.')
type teamConfig = {
  @description('Short lowercase team name, for example alpha. Derives product, subscription, project and connection names.')
  @minLength(2)
  @maxLength(20)
  name: string

  @description('Token-per-minute budget enforced by the gateway for this team product. Size it from your own measurements.')
  @minValue(1)
  tokensPerMinute: int

  @description('Microsoft Entra object IDs that receive Foundry User on this team project only. Prefer one group per team.')
  memberPrincipalIds: string[]

  @description('Principal type of memberPrincipalIds. Defaults to Group.')
  memberPrincipalType: ('Group' | 'User' | 'ServicePrincipal')?
}

@description('Azure region for every resource in this deployment.')
param location string = resourceGroup().location

@description('Short prefix used to derive resource names.')
@minLength(3)
@maxLength(12)
param namePrefix string = 'contoso'

@description('Create the Foundry account and model deployment. Set false to reuse an existing account (and deployment) in this resource group.')
param createFoundryAccount bool = true

@description('Foundry (AIServices) account name. Also used as the custom subdomain when the account is created here.')
@minLength(2)
@maxLength(64)
param foundryAccountName string = 'aif-${namePrefix}-${uniqueString(resourceGroup().id)}'

@description('Public network access for a newly created Foundry account. Use Disabled with private networking; see the secured landing zone asset.')
@allowed([
  'Enabled'
  'Disabled'
])
param foundryPublicNetworkAccess string = 'Enabled'

@description('Name of the shared model deployment that every team calls through the gateway.')
param modelDeploymentName string = 'chat'

@description('Model name for a newly created deployment.')
param modelName string = 'gpt-4.1-mini'

@description('Model version for a newly created deployment. Check model availability in your region.')
param modelVersion string = '2025-04-14'

@description('Deployment SKU for a newly created deployment.')
param modelSkuName string = 'GlobalStandard'

@description('Deployment capacity (in units of the deployment SKU). The sum of team budgets should fit inside it.')
@minValue(1)
param modelCapacity int = 10

@description('API Management service name (globally unique).')
@minLength(1)
@maxLength(50)
param apimName string = 'apim-${namePrefix}-aigw-${uniqueString(resourceGroup().id)}'

@description('API Management SKU. The v2 tiers support the token limit policy and managed identity.')
@allowed([
  'BasicV2'
  'StandardV2'
])
param apimSku string = 'BasicV2'

@description('Publisher email shown in APIM. Use a shared team mailbox.')
param publisherEmail string = 'platform-team@example.com'

@description('Publisher name shown in APIM.')
param publisherName string = 'Contoso Platform Team'

@description('Path prefix of the gateway API. Clients use <gatewayUrl>/<apiPathPrefix> as their Azure OpenAI endpoint.')
param apiPathPrefix string = 'aigw'

@description('Model deployments that the gateway exposes. Requests for any other deployment get 404 before spending budget.')
param allowedModelDeployments string[] = [
  modelDeploymentName
]

@description('Teams that share the deployment. Each entry produces a product, subscription, project, connection and role assignments.')
@minLength(1)
param teams teamConfig[]

@description('Role definition ID (GUID only) granted to the APIM managed identity on the Foundry account, for example Cognitive Services User or the narrower Cognitive Services OpenAI User. Look it up in your tenant; see infra/README.md.')
@minLength(36)
@maxLength(36)
param apimModelInferenceRoleDefinitionId string

@description('Role definition ID (GUID only) of the Foundry User built-in role, granted to team members on their own project.')
@minLength(36)
@maxLength(36)
param foundryUserRoleDefinitionId string = '53ca6127-db72-4b80-b1b0-d745d6d5456d'

@description('Deploy Log Analytics, Application Insights, an APIM logger, API diagnostics with custom metrics and the per-team token metric policy.')
param enableObservability bool = true

@description('Tags applied to every resource.')
param tags object = {
  workload: 'multi-team-model-gateway'
}

var gatewayApiPath = apiPathPrefix

module foundry 'modules/foundry-account.bicep' = if (createFoundryAccount) {
  name: 'mtmg-foundry-account'
  params: {
    location: location
    accountName: foundryAccountName
    publicNetworkAccess: foundryPublicNetworkAccess
    modelDeploymentName: modelDeploymentName
    modelName: modelName
    modelVersion: modelVersion
    modelSkuName: modelSkuName
    modelCapacity: modelCapacity
    tags: tags
  }
}

module gateway 'modules/apim-gateway.bicep' = {
  name: 'mtmg-apim-gateway'
  params: {
    location: location
    namePrefix: namePrefix
    apimName: apimName
    apimSku: apimSku
    publisherEmail: publisherEmail
    publisherName: publisherName
    foundryAccountName: foundryAccountName
    apiPathPrefix: gatewayApiPath
    allowedModelDeployments: allowedModelDeployments
    enableObservability: enableObservability
    tags: tags
  }
  dependsOn: [
    foundry
  ]
}

module apimModelAccess 'modules/role-assignment.bicep' = {
  name: 'mtmg-apim-model-access'
  params: {
    foundryAccountName: foundryAccountName
    principalId: gateway.outputs.principalId
    roleDefinitionId: apimModelInferenceRoleDefinitionId
  }
}

// Projects under one account are created one at a time to avoid concurrent updates to the parent account.
@batchSize(1)
module team 'modules/team.bicep' = [
  for t in teams: {
    name: 'mtmg-team-${t.name}'
    params: {
      location: location
      teamName: t.name
      tokensPerMinute: t.tokensPerMinute
      memberPrincipalIds: t.memberPrincipalIds
      memberPrincipalType: t.?memberPrincipalType ?? 'Group'
      apimName: gateway.outputs.apimName
      apimId: gateway.outputs.apimId
      apiName: gateway.outputs.apiName
      gatewayTarget: '${gateway.outputs.gatewayUrl}/${gatewayApiPath}'
      foundryAccountName: foundryAccountName
      foundryUserRoleDefinitionId: foundryUserRoleDefinitionId
      tags: tags
    }
    dependsOn: [
      apimModelAccess
    ]
  }
]

@description('API Management service name.')
output apimName string = gateway.outputs.apimName

@description('APIM gateway base URL (properties.gatewayUrl).')
output gatewayUrl string = gateway.outputs.gatewayUrl

@description('Azure OpenAI-compatible endpoint that every team connection targets.')
output gatewayTarget string = '${gateway.outputs.gatewayUrl}/${gatewayApiPath}'

@description('Foundry account that hosts the shared deployment and the team projects.')
output foundryAccountName string = foundryAccountName

@description('Shared model deployment name exposed through the gateway.')
output modelDeploymentName string = modelDeploymentName

@description('Application Insights resource that receives the per-team token metric (empty when observability is disabled).')
output appInsightsName string = gateway.outputs.appInsightsName

@description('Per-team wiring. No keys are output; teams read their key through their project connection.')
output teams array = [
  for (t, i) in teams: {
    team: t.name
    productName: team[i].outputs.productName
    subscriptionName: team[i].outputs.subscriptionName
    projectName: team[i].outputs.projectName
    projectEndpoint: team[i].outputs.projectEndpoint
    connectionName: team[i].outputs.connectionName
  }
]
