// Private-by-default Microsoft Foundry account and project (Stage 2).
//
// Adapted, in simplified and sanitized form, from the network-secured agent
// setup pattern in the public foundry-samples repository
// (infrastructure-setup-bicep/19-private-network-agent-tools). That pattern is
// the right starting point when agent tools (for example MCP servers) also live
// behind the VNet; patterns that exclude VNet-hosted tools are not.
//
// What this template owns:
//   * The foundation VNet and its FULL subnet array (agent capability host
//     subnet, internal MCP Container Apps subnet, private endpoint subnet).
//     Because ARM treats the inline subnet array as authoritative, do not add
//     other subnets (for example a CI runner subnet) out of band; place the
//     operator runner in a separate, peered VNet instead.
//   * Foundry account (publicNetworkAccess Disabled, disableLocalAuth true)
//     with the agent subnet injected, one project, and one model deployment.
//   * Agent dependencies (AI Search, Storage, Cosmos DB) with public access
//     disabled and local auth disabled, plus private endpoints and private DNS.
//   * Project connections, role assignments, and the project capability host.
//
// Expect the capability host to take 30-35 minutes on first deployment. Allow
// at least 75 minutes for the ARM deployment and 90 minutes for a CI job.

targetScope = 'resourceGroup'

@description('Deployment region. Must support Foundry Agent Service network injection.')
param location string = resourceGroup().location

@description('Short workload prefix used to derive resource names, for example "contoso".')
@minLength(3)
@maxLength(12)
param name string

@description('Foundry project name, for example "team-a-agents". One project per application team or workload boundary.')
@minLength(3)
param projectName string

@description('Address space for the foundation VNet.')
param vnetAddressPrefix string = '10.60.0.0/16'

@description('Agent capability host subnet (delegated to Microsoft.App/environments).')
param agentSubnetPrefix string = '10.60.0.0/24'

@description('Private endpoint subnet.')
param privateEndpointSubnetPrefix string = '10.60.1.0/24'

@description('Internal MCP Container Apps environment subnet (delegated to Microsoft.App/environments, /27 or larger).')
param mcpSubnetPrefix string = '10.60.2.0/24'

@description('Model to deploy, for example "gpt-4.1".')
@minLength(1)
param modelName string

@description('Model version available in the selected region.')
@minLength(1)
param modelVersion string

@description('Deployment SKU, for example "GlobalStandard".')
param modelSkuName string = 'GlobalStandard'

@description('Deployment capacity in thousands of tokens per minute.')
param modelCapacity int = 30

@description('Optional principal id of the application/release managed identity. When set, it receives the project-scoped Foundry User role and nothing broader.')
param releasePrincipalId string = ''

param tags object = {}

var suffix = uniqueString(resourceGroup().id)
var accountName = toLower('${name}-foundry-${suffix}')
var searchName = toLower('${name}-search-${suffix}')
var storageName = '${take(toLower(replace(name, '-', '')), 9)}st${suffix}'
var cosmosName = toLower('${name}-cosmos-${suffix}')
var vnetName = '${name}-foundation-vnet'

// Built-in role definition ids (public, tenant-independent).
var roles = {
  foundryUser: '53ca6127-db72-4b80-b1b0-d745d6d5456d'
  cosmosDbOperator: '230815da-be43-4aae-9cb4-875f7bd000aa'
  storageBlobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  storageBlobDataOwner: 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
  searchIndexDataContributor: '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
  searchServiceContributor: '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
}
var cosmosBuiltInDataContributor = '00000000-0000-0000-0000-000000000002'

var privateDnsZoneNames = [
  'privatelink.cognitiveservices.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.services.ai.azure.com'
  'privatelink.search.windows.net'
  'privatelink.blob.${environment().suffixes.storage}'
  'privatelink.documents.azure.com'
]

// ---------------------------------------------------------------------------
// Network: one VNet, three subnets, owned in full by this template.
// ---------------------------------------------------------------------------

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        vnetAddressPrefix
      ]
    }
    subnets: [
      {
        name: 'agent-subnet'
        properties: {
          addressPrefix: agentSubnetPrefix
          delegations: [
            {
              name: 'Microsoft.App.environments'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
      {
        name: 'pe-subnet'
        properties: {
          addressPrefix: privateEndpointSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: 'mcp-subnet'
        properties: {
          addressPrefix: mcpSubnetPrefix
          delegations: [
            {
              name: 'Microsoft.App.environments'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
    ]
  }
}

var agentSubnetId = '${vnet.id}/subnets/agent-subnet'
var privateEndpointSubnetId = '${vnet.id}/subnets/pe-subnet'
var mcpSubnetId = '${vnet.id}/subnets/mcp-subnet'

// ---------------------------------------------------------------------------
// Foundry account, model deployment, and project.
// ---------------------------------------------------------------------------

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    // Entra ID only; no API keys.
    disableLocalAuth: true
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
    networkInjections: [
      {
        scenario: 'agent'
        subnetArmId: agentSubnetId
        useMicrosoftManagedNetwork: false
      }
    ]
  }
}

resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: account
  name: modelName
  sku: {
    name: modelSkuName
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
  }
}

// ---------------------------------------------------------------------------
// Agent dependencies: private, Entra-only.
// ---------------------------------------------------------------------------

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: searchName
  location: location
  tags: tags
  sku: {
    name: 'standard'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    publicNetworkAccess: 'disabled'
    disableLocalAuth: true
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_ZRS'
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
  }
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: cosmosName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    disableLocalAuth: true
    publicNetworkAccess: 'Disabled'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Private DNS zones (linked to the foundation VNet) and private endpoints.
// Link the same zones to the operator VNet with operator-connectivity.bicep.
// ---------------------------------------------------------------------------

resource zones 'Microsoft.Network/privateDnsZones@2024-06-01' = [for zoneName in privateDnsZoneNames: {
  name: zoneName
  location: 'global'
  tags: tags
}]

resource zoneLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for (zoneName, i) in privateDnsZoneNames: {
  parent: zones[i]
  name: '${name}-foundation-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}]

var endpoints = [
  {
    name: 'foundry'
    serviceId: account.id
    groupId: 'account'
    zoneIndexes: [0, 1, 2]
  }
  {
    name: 'search'
    serviceId: search.id
    groupId: 'searchService'
    zoneIndexes: [3]
  }
  {
    name: 'blob'
    serviceId: storage.id
    groupId: 'blob'
    zoneIndexes: [4]
  }
  {
    name: 'cosmos'
    serviceId: cosmos.id
    groupId: 'Sql'
    zoneIndexes: [5]
  }
]

resource privateEndpoints 'Microsoft.Network/privateEndpoints@2024-05-01' = [for ep in endpoints: {
  name: '${name}-${ep.name}-pe'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: ep.name
        properties: {
          privateLinkServiceId: ep.serviceId
          groupIds: [
            ep.groupId
          ]
        }
      }
    ]
  }
}]

resource dnsZoneGroups 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = [for (ep, i) in endpoints: {
  parent: privateEndpoints[i]
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [for zi in ep.zoneIndexes: {
      name: replace(privateDnsZoneNames[zi], '.', '-')
      properties: {
        privateDnsZoneId: resourceId('Microsoft.Network/privateDnsZones', privateDnsZoneNames[zi])
      }
    }]
  }
  dependsOn: [
    zones
  ]
}]

// ---------------------------------------------------------------------------
// Project, connections, and capability host.
// ---------------------------------------------------------------------------

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: account
  name: projectName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    displayName: projectName
    description: 'Network-isolated agent project.'
  }
  dependsOn: [
    dnsZoneGroups
  ]
}

resource searchConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: search.name
  properties: {
    category: 'CognitiveSearch'
    target: 'https://${search.name}.search.windows.net'
    authType: 'AAD'
    metadata: {
      ApiType: 'Azure'
      ResourceId: search.id
      location: search.location
    }
  }
}

resource storageConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: storage.name
  properties: {
    category: 'AzureStorageAccount'
    target: storage.properties.primaryEndpoints.blob
    authType: 'AAD'
    metadata: {
      ApiType: 'Azure'
      ResourceId: storage.id
      location: storage.location
    }
  }
}

resource cosmosConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: cosmos.name
  properties: {
    category: 'CosmosDB'
    target: cosmos.properties.documentEndpoint
    authType: 'AAD'
    metadata: {
      ApiType: 'Azure'
      ResourceId: cosmos.id
      location: cosmos.location
    }
  }
}

// Control-plane and service-level roles the capability host needs up front.
resource cosmosOperator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: cosmos
  name: guid(cosmos.id, project.id, roles.cosmosDbOperator)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cosmosDbOperator)
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource storageContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, project.id, roles.storageBlobDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageBlobDataContributor)
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource searchIndexContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, project.id, roles.searchIndexDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchIndexDataContributor)
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource searchServiceContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, project.id, roles.searchServiceContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchServiceContributor)
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource capabilityHost 'Microsoft.CognitiveServices/accounts/projects/capabilityHosts@2025-04-01-preview' = {
  parent: project
  name: 'agents'
  properties: {
    capabilityHostKind: 'Agents'
    vectorStoreConnections: [
      searchConnection.name
    ]
    storageConnections: [
      storageConnection.name
    ]
    threadStorageConnections: [
      cosmosConnection.name
    ]
  }
  dependsOn: [
    cosmosOperator
    storageContributor
    searchIndexContributor
    searchServiceContributor
    modelDeployment
  ]
}

// Data-plane grants scoped to what the capability host created. The project
// workspace id is formatted as a GUID to match container naming.
// internalId is returned by the service but not yet in the published type.
#disable-next-line BCP053
var internalId = project.properties.internalId
var workspaceGuid = '${substring(internalId, 0, 8)}-${substring(internalId, 8, 4)}-${substring(internalId, 12, 4)}-${substring(internalId, 16, 4)}-${substring(internalId, 20, 12)}'

resource storageOwnerScoped 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, project.id, roles.storageBlobDataOwner, 'agent-containers')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageBlobDataOwner)
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
    conditionVersion: '2.0'
    condition: '((!(ActionMatches{\'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/read\'}) AND !(ActionMatches{\'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/filter/action\'}) AND !(ActionMatches{\'Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/write\'})) OR (@Resource[Microsoft.Storage/storageAccounts/blobServices/containers:name] StringStartsWithIgnoreCase \'${workspaceGuid}\' AND @Resource[Microsoft.Storage/storageAccounts/blobServices/containers:name] StringLikeIgnoreCase \'*-azureml-agent\'))'
  }
  dependsOn: [
    capabilityHost
  ]
}

resource cosmosDataScoped 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmos
  name: guid(cosmos.id, project.id, cosmosBuiltInDataContributor, 'enterpriseMemory')
  properties: {
    principalId: project.identity.principalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosBuiltInDataContributor}'
    scope: '${cosmos.id}/dbs/enterpriseMemory'
  }
  dependsOn: [
    capabilityHost
  ]
}

// Least-privilege release identity: Foundry User at project scope only. IAM,
// RAI policy, and Toolbox registration stay with the platform team.
resource releaseFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(releasePrincipalId)) {
  scope: project
  name: guid(project.id, releasePrincipalId, roles.foundryUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.foundryUser)
    principalId: releasePrincipalId
    principalType: 'ServicePrincipal'
  }
}

output accountName string = account.name
output accountResourceId string = account.id
output projectName string = project.name
output projectResourceId string = project.id
output projectEndpoint string = 'https://${accountName}.services.ai.azure.com/api/projects/${project.name}'
output modelDeploymentName string = modelDeployment.name
output virtualNetworkName string = vnet.name
output virtualNetworkId string = vnet.id
output agentSubnetId string = agentSubnetId
output mcpSubnetId string = mcpSubnetId
output privateEndpointSubnetId string = privateEndpointSubnetId
output privateDnsZoneNames array = privateDnsZoneNames
