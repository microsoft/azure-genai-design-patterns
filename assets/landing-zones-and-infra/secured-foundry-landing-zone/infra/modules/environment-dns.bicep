// Publishes an internal Container Apps environment's generated default domain
// through a private DNS zone. Internal environments do not register their own
// names; without this zone (and a link from every VNet that must call the
// apps), clients fail name resolution even though routing works.

@description('The Container Apps environment default domain, for example "<random>.<region>.azurecontainerapps.io".')
@minLength(1)
param zoneName string

@description('Static (internal load balancer) IP of the Container Apps environment.')
@minLength(7)
param staticIp string

@description('Resource id of the virtual network to link.')
@minLength(1)
param virtualNetworkId string

param linkName string
param tags object = {}

resource zone 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: zoneName
  location: 'global'
  tags: tags
}

resource link 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: zone
  name: linkName
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetworkId
    }
  }
}

resource wildcard 'Microsoft.Network/privateDnsZones/A@2024-06-01' = {
  parent: zone
  name: '*'
  properties: {
    ttl: 60
    aRecords: [
      {
        ipv4Address: staticIp
      }
    ]
  }
}

output zoneName string = zone.name
