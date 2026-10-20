// Connects a separate private operator/runner VNet to the foundation VNet.
//
// Why a separate VNet: the foundation template owns its complete subnet array,
// and both its agent and MCP subnets are delegated to Microsoft.App/environments
// (they cannot host a VM NIC). Adding a runner subnet out of band would be
// removed by the next foundation deployment.
//
// Peering alone does NOT provide private name resolution. Every private DNS
// zone that the operator must resolve (Foundry, Search, Storage, Cosmos DB,
// Key Vault, ACR, and the internal Container Apps environment's generated
// default domain) needs an explicit virtual network link to the operator VNet.
//
// This is private access for operators, not closed egress. Pair it with an
// approved egress policy for the runner (for example NAT + firewall rules
// allowing only identity, package, and source-control endpoints).

targetScope = 'resourceGroup'

@description('Prefix for peering and link names, for example "contoso".')
@minLength(3)
param name string

@description('Name of the foundation VNet (in this resource group).')
@minLength(1)
param foundationVnetName string

@description('Name of the operator/runner VNet (in this resource group). Its address space must not overlap the foundation VNet.')
@minLength(1)
param operatorVnetName string

@description('Private DNS zone names (in this resource group) to link to the operator VNet. Combine the privateDnsZoneNames outputs of foundation.bicep and private-foundry-project.bicep.')
@minLength(1)
param privateDnsZoneNames array

resource foundation 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: foundationVnetName
}

resource operator 'Microsoft.Network/virtualNetworks@2024-05-01' existing = {
  name: operatorVnetName
}

resource operatorToFoundation 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2024-05-01' = {
  parent: operator
  name: '${name}-operator-to-foundation'
  properties: {
    remoteVirtualNetwork: {
      id: foundation.id
    }
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: false
    allowGatewayTransit: false
    useRemoteGateways: false
  }
}

resource foundationToOperator 'Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2024-05-01' = {
  parent: foundation
  name: '${name}-foundation-to-operator'
  properties: {
    remoteVirtualNetwork: {
      id: operator.id
    }
    allowVirtualNetworkAccess: true
    allowForwardedTraffic: false
    allowGatewayTransit: false
    useRemoteGateways: false
  }
}

resource zones 'Microsoft.Network/privateDnsZones@2024-06-01' existing = [for zoneName in privateDnsZoneNames: {
  name: zoneName
}]

resource links 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = [for (zoneName, i) in privateDnsZoneNames: {
  parent: zones[i]
  name: '${name}-operator-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: operator.id
    }
  }
}]

output operatorVnetId string = operator.id
output foundationVnetId string = foundation.id
output linkedDnsZones array = privateDnsZoneNames
