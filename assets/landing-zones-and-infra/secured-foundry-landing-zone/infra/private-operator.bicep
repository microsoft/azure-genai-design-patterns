// Private operator VNet and runner VM.
//
// The operator runner executes data-plane steps (agent version creation,
// Toolbox registration, smoke tests, evaluation runs) against a Foundry
// account whose public network access is disabled. It must therefore resolve
// and reach private endpoints. It cannot live inside the foundation VNet:
//   * the agent and MCP subnets are delegated to Microsoft.App/environments
//     and cannot host a VM NIC, and
//   * the foundation template owns the complete subnet array; an extra subnet
//     added out-of-band is deleted on the next foundation deployment.
// The runner therefore lives in its own VNet, peered to the foundation VNet by
// operator-connectivity.bicep, with every privatelink zone linked to both.
//
// Security posture:
//   * no public IP on the NIC and an inbound DenyAll NSG rule,
//   * outbound only through a NAT gateway (subnet defaultOutboundAccess=false),
//   * SSH key authentication only; administration through Azure Run Command or
//     Bastion, never through an opened port,
//   * Trusted Launch (secure boot + vTPM),
//   * a system-assigned identity that receives NO deployment roles. Deployment
//     identities are federated to the CI system (OIDC); they are never attached
//     to this VM, so another workload on the VM cannot mint their tokens via IMDS.
//
// cloud-init installs pinned dependencies (GitHub Actions runner archive with
// SHA-256 verification, Azure CLI, Docker). It does not register the runner;
// registration needs a short-lived token and is an explicit operator step.

targetScope = 'resourceGroup'

param location string = resourceGroup().location

@minLength(2)
@maxLength(40)
@description('Short lowercase prefix for resource names.')
param name string

@description('Create the operator VNet. Set false to place the runner in an existing VNet (operatorVnetName must then exist in this resource group).')
param createVirtualNetwork bool = true

@description('Name of the operator VNet to create or reuse.')
param operatorVnetName string = '${name}-operator-vnet'

@description('Operator VNet address space. Must not overlap the foundation VNet or any on-premises range.')
param operatorVnetPrefix string = '10.70.0.0/16'

@description('Runner subnet prefix inside the operator VNet.')
param runnerSubnetPrefix string = '10.70.0.0/24'

@description('Runner subnet name.')
param runnerSubnetName string = 'runner-subnet'

@description('VM size. Use a size that supports accelerated networking and Trusted Launch.')
param vmSize string = 'Standard_D2as_v6'

@allowed([
  ''
  '1'
  '2'
  '3'
])
@description('Availability zone for the VM, NAT gateway and NAT public IP. Empty for regions without zones.')
param zone string = '1'

@description('Local administrator username. Interactive login is SSH-key only.')
param adminUsername string = 'azureops'

@secure()
@description('SSH public key (ssh-ed25519 or ssh-rsa). Never commit it to parameter files.')
param adminPublicKey string

@description('Ubuntu 24.04 LTS image version. Pin a specific version for reproducible builds.')
param imageVersion string = 'latest'

@minValue(30)
@maxValue(1024)
@description('OS disk size in GB.')
param osDiskSizeGb int = 64

@description('Tags applied to every resource.')
param tags object = {}

var zones = empty(zone) ? [] : [
  zone
]
var vmName = take('${name}-runner', 64)

resource natPublicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: '${name}-operator-nat-pip'
  location: location
  tags: tags
  zones: zones
  sku: {
    name: 'Standard'
    tier: 'Regional'
  }
  properties: {
    publicIPAddressVersion: 'IPv4'
    publicIPAllocationMethod: 'Static'
    idleTimeoutInMinutes: 10
  }
}

resource natGateway 'Microsoft.Network/natGateways@2024-05-01' = {
  name: '${name}-operator-nat'
  location: location
  tags: tags
  zones: zones
  sku: {
    name: 'Standard'
  }
  properties: {
    idleTimeoutInMinutes: 10
    publicIpAddresses: [
      {
        id: natPublicIp.id
      }
    ]
  }
}

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${name}-operator-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'DenyAllInbound'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: '*'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
          description: 'No inbound access. Administer through Run Command or Bastion.'
        }
      }
    ]
  }
}

resource newVnet 'Microsoft.Network/virtualNetworks@2024-05-01' = if (createVirtualNetwork) {
  name: operatorVnetName
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        operatorVnetPrefix
      ]
    }
    subnets: [
      {
        name: runnerSubnetName
        properties: {
          addressPrefix: runnerSubnetPrefix
          defaultOutboundAccess: false
          natGateway: {
            id: natGateway.id
          }
          networkSecurityGroup: {
            id: nsg.id
          }
        }
      }
    ]
  }
}

resource existingVnet 'Microsoft.Network/virtualNetworks@2024-05-01' existing = if (!createVirtualNetwork) {
  name: operatorVnetName
}

resource existingRunnerSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' = if (!createVirtualNetwork) {
  parent: existingVnet
  name: runnerSubnetName
  properties: {
    addressPrefix: runnerSubnetPrefix
    defaultOutboundAccess: false
    natGateway: {
      id: natGateway.id
    }
    networkSecurityGroup: {
      id: nsg.id
    }
  }
}

var runnerSubnetId = createVirtualNetwork
  ? resourceId('Microsoft.Network/virtualNetworks/subnets', operatorVnetName, runnerSubnetName)
  : existingRunnerSubnet.id
var operatorVnetId = resourceId('Microsoft.Network/virtualNetworks', operatorVnetName)

resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: '${vmName}-nic'
  location: location
  tags: tags
  properties: {
    enableAcceleratedNetworking: true
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          privateIPAllocationMethod: 'Dynamic'
          subnet: {
            id: runnerSubnetId
          }
        }
      }
    ]
  }
  dependsOn: [
    newVnet
  ]
}

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: vmName
  location: location
  tags: tags
  zones: zones
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    hardwareProfile: {
      vmSize: vmSize
    }
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: {
        secureBootEnabled: true
        vTpmEnabled: true
      }
    }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: 'ubuntu-24_04-lts'
        sku: 'server'
        version: imageVersion
      }
      osDisk: {
        name: '${vmName}-osdisk'
        createOption: 'FromImage'
        deleteOption: 'Delete'
        diskSizeGB: osDiskSizeGb
        managedDisk: {
          storageAccountType: 'StandardSSD_LRS'
        }
      }
    }
    osProfile: {
      computerName: take(replace(vmName, '_', '-'), 15)
      adminUsername: adminUsername
      customData: base64(loadTextContent('private-operator-cloud-init.yaml'))
      linuxConfiguration: {
        disablePasswordAuthentication: true
        provisionVMAgent: true
        patchSettings: {
          patchMode: 'AutomaticByPlatform'
          assessmentMode: 'AutomaticByPlatform'
        }
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: adminPublicKey
            }
          ]
        }
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: nic.id
          properties: {
            deleteOption: 'Delete'
          }
        }
      ]
    }
    diagnosticsProfile: {
      bootDiagnostics: {
        enabled: true
      }
    }
  }
}

output vmResourceId string = vm.id
output vmName string = vm.name
output principalId string = vm.identity.principalId
output privateIp string = nic.properties.ipConfigurations[0].properties.privateIPAddress
output operatorVnetId string = operatorVnetId
output operatorVnetName string = operatorVnetName
output natPublicIp string = natPublicIp.properties.ipAddress
output networkBoundary string = 'no public IP on NIC; inbound DenyAll; outbound via NAT gateway only; no deployment identity attached'
