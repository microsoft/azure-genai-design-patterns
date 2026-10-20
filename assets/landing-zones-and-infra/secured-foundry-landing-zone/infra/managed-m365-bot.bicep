// Managed Azure Bot Service resource that bridges Microsoft 365 (Teams /
// Microsoft 365 Copilot) Activity traffic to a published Foundry agent.
//
// This is the ONE scoped public-ingress exception in an otherwise private
// landing zone. Microsoft 365 channels deliver Activity Protocol messages from
// Microsoft-operated infrastructure; they cannot traverse a customer VNet. The
// exception is bounded as follows:
//   * The bot resource has publicNetworkAccess disabled for its own management
//     data plane; only the channel service calls the messaging endpoint.
//   * The messaging endpoint is the Foundry-managed Activity Protocol endpoint
//     for one published agent application (not a customer-hosted web app).
//   * The bot identity is a single-tenant Entra application (the published
//     agent's client ID). No multi-tenant app and no client secret is created
//     here.
//   * Endpoint authorization stays at BotServiceRbac unless a reviewed decision
//     record approves BotServiceTenant (see docs/oauth-passthrough-and-m365-publishing.md).
//
// Prefer the Foundry "publish to Microsoft 365" flow, which creates the bot for
// you. Use this template when platform policy requires the bot resource to be
// declared, tagged and reviewed as code alongside the rest of the landing zone.

targetScope = 'resourceGroup'

@minLength(2)
@maxLength(40)
@description('Short lowercase prefix for resource names.')
param name string

@description('Display name shown in Teams and Microsoft 365 Copilot.')
param displayName string

@description('Foundry-managed Activity Protocol endpoint for the published agent application (https://<account>.services.ai.azure.com/api/projects/<project>/applications/<app>/protocols/activityprotocol?api-version=...).')
param activityEndpoint string

@minLength(36)
@maxLength(36)
@description('Client (application) ID of the published agent identity.')
param agentClientId string

@minLength(36)
@maxLength(36)
@description('Entra tenant ID that owns the agent identity.')
param tenantId string

@allowed([
  'F0'
  'S1'
])
@description('Bot Service SKU. F0 is sufficient for Teams and Microsoft 365 channels; S1 is needed only for premium channels.')
param sku string = 'F0'

@description('Enable the Microsoft Teams channel.')
param enableTeamsChannel bool = true

@description('Enable the Microsoft 365 extensions channel used by Microsoft 365 Copilot. Requires tenant admin approval to surface the agent.')
param enableM365ExtensionChannel bool = false

@description('Tags applied to every resource.')
param tags object = {}

var botName = toLower('${name}-bot-${uniqueString(resourceGroup().id, name)}')

resource bot 'Microsoft.BotService/botServices@2022-09-15' = {
  name: take(botName, 64)
  location: 'global'
  kind: 'azurebot'
  tags: tags
  sku: {
    name: sku
  }
  properties: {
    displayName: displayName
    endpoint: activityEndpoint
    msaAppId: agentClientId
    msaAppTenantId: tenantId
    msaAppType: 'SingleTenant'
    publicNetworkAccess: 'Disabled'
    disableLocalAuth: true
    isStreamingSupported: false
  }
}

resource teamsChannel 'Microsoft.BotService/botServices/channels@2022-09-15' = if (enableTeamsChannel) {
  parent: bot
  name: 'MsTeamsChannel'
  location: 'global'
  properties: {
    channelName: 'MsTeamsChannel'
    properties: {
      isEnabled: true
      acceptedTerms: true
    }
  }
}

resource m365Channel 'Microsoft.BotService/botServices/channels@2022-09-15' = if (enableM365ExtensionChannel) {
  parent: bot
  name: 'M365Extensions'
  location: 'global'
  properties: {
    channelName: 'M365Extensions'
  }
}

output botName string = bot.name
output botResourceId string = bot.id
output botClientId string = agentClientId
output activityEndpoint string = activityEndpoint
output networkBoundary string = 'scoped public Activity ingress via Microsoft 365 channel service; single-tenant identity; bot management plane public access disabled'
