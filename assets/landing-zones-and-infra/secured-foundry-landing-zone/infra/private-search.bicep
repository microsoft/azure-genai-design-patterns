// Private Azure AI Search service for Foundry IQ / knowledge-base grounding.
//
// Use this template when a project needs a dedicated, governed retrieval index
// (for example a team-owned knowledge base) in addition to the agent-dependency
// Search service that private-foundry-project.bicep creates for the capability
// host. The service:
//   * disables public network access and local (API key) authentication,
//   * is reachable only through a private endpoint in the foundation VNet's
//     private-endpoint subnet,
//   * resolves through privatelink.search.windows.net linked to that VNet, and
//   * grants the Foundry project managed identity data-plane RBAC only.
//
// The Search service may live in a different region from the Foundry VNet when
// the required SKU or semantic ranker is not available locally. The private
// endpoint always stays in the VNet region (`location`). Review latency, data
// residency and cross-region egress before choosing a different searchLocation.

targetScope = 'resourceGroup'

@description('Private endpoint region. Must match the foundation VNet region.')
param location string = resourceGroup().location

@description('Search service region. Defaults to the VNet region; override only after a residency and latency review.')
param searchLocation string = location

@minLength(2)
@maxLength(40)
@description('Short lowercase prefix for resource names (letters, digits, hyphen).')
param name string

@description('Resource ID of the private-endpoint subnet in the foundation VNet (pe-subnet).')
param privateEndpointSubnetId string

@description('Resource ID of the foundation VNet that must resolve the Search private endpoint.')
param virtualNetworkId string

@description('Reuse an existing privatelink.search.windows.net zone in this resource group (true when private-foundry-project.bicep already created it).')
param useExistingSearchZone bool = true

@description('Object ID of the Foundry project system-assigned identity that reads and writes indexes. Leave empty to skip data-plane RBAC.')
param projectPrincipalId string = ''

@description('Grant the project identity write access to indexes (index build pipelines). Read-only grounding needs only Search Index Data Reader.')
param grantIndexWrite bool = false

@allowed([
  'basic'
  'standard'
  'standard2'
  'standard3'
])
@description('Search SKU. Private endpoints require basic or higher.')
param sku string = 'standard'

@minValue(1)
@maxValue(12)
@description('Replica count. Use 2+ for read SLA and 3+ for read/write SLA.')
param replicaCount int = 1

@minValue(1)
@maxValue(12)
@description('Partition count.')
param partitionCount int = 1

@allowed([
  'disabled'
  'free'
  'standard'
])
@description('Semantic ranker plan.')
param semanticSearch string = 'free'

@description('Tags applied to every resource.')
param tags object = {}

var searchName = toLower('${name}-iq-${uniqueString(resourceGroup().id, name)}')
var searchZoneName = 'privatelink.search.windows.net'

var searchIndexDataReaderRoleId = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var searchIndexDataContributorRoleId = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
var searchServiceContributorRoleId = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: take(searchName, 60)
  location: searchLocation
  tags: tags
  sku: {
    name: sku
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    replicaCount: replicaCount
    partitionCount: partitionCount
    hostingMode: 'default'
    publicNetworkAccess: 'disabled'
    disableLocalAuth: true
    semanticSearch: semanticSearch
    networkRuleSet: {
      bypass: 'None'
      ipRules: []
    }
  }
}

resource newSearchZone 'Microsoft.Network/privateDnsZones@2020-06-01' = if (!useExistingSearchZone) {
  name: searchZoneName
  location: 'global'
  tags: tags
}

resource existingSearchZone 'Microsoft.Network/privateDnsZones@2020-06-01' existing = if (useExistingSearchZone) {
  name: searchZoneName
}

resource newSearchZoneLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = if (!useExistingSearchZone) {
  parent: newSearchZone
  name: '${name}-search-link'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetworkId
    }
  }
}

resource searchPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: '${search.name}-pe'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${search.name}-connection'
        properties: {
          privateLinkServiceId: search.id
          groupIds: [
            'searchService'
          ]
        }
      }
    ]
  }
}

resource searchDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: searchPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'search'
        properties: {
          privateDnsZoneId: useExistingSearchZone ? existingSearchZone.id : newSearchZone.id
        }
      }
    ]
  }
  dependsOn: [
    newSearchZoneLink
  ]
}

resource indexReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(projectPrincipalId)) {
  name: guid(search.id, projectPrincipalId, searchIndexDataReaderRoleId)
  scope: search
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataReaderRoleId)
  }
}

resource indexContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(projectPrincipalId) && grantIndexWrite) {
  name: guid(search.id, projectPrincipalId, searchIndexDataContributorRoleId)
  scope: search
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributorRoleId)
  }
}

resource serviceContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(projectPrincipalId) && grantIndexWrite) {
  name: guid(search.id, projectPrincipalId, searchServiceContributorRoleId)
  scope: search
  properties: {
    principalId: projectPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributorRoleId)
  }
}

output searchName string = search.name
output searchResourceId string = search.id
output searchRegion string = search.location
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output searchPrincipalId string = search.identity.principalId
output privateDnsZoneName string = searchZoneName
output networkBoundary string = 'public network disabled; local auth disabled; private endpoint in ${location}; data-plane RBAC only'
