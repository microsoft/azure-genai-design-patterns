// Multi-Tenant MCP Gateway on Azure API Management: deployment skeleton.
//
// Illustrative only. NOT production-hardened. Check current docs.
// - API versions, SKU and tier availability, VNet requirements, and the MCP server resource shape
//   change over time. MCP server support in API Management uses a preview API version here.
// - No sizing, limit, timeout, or retention values are baked in. Every such value is a required
//   parameter so that you set it from your own load tests and policies.
// - See infra/README.md for the deployment order, prerequisites, and known gaps.

targetScope = 'resourceGroup'

// ---------------------------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------------------------

@description('Azure region for all resources except API Center.')
param location string = resourceGroup().location

@description('Region for API Center. API Center is available in a subset of regions. Check current docs.')
param apiCenterLocation string = location

@description('Short lowercase prefix used in resource names.')
@minLength(3)
@maxLength(10)
param namePrefix string

@description('Public host name that MCP clients call, for example mcp.contoso.com.')
param mcpHostName string = 'mcp.contoso.com'

@description('Versionless Key Vault secret ID of the TLS certificate for the public listener.')
param tlsCertificateKeyVaultUri string

@description('API Management SKU. Classic internal VNet injection needs Developer (non-production) or Premium. v2 tiers use different networking options. Check current docs.')
@allowed([
  'Developer'
  'Premium'
])
param apimSkuName string = 'Premium'

@description('API Management units. No default: size from your own load tests.')
param apimSkuCapacity int

@description('Publisher email shown in API Management.')
param publisherEmail string = 'platform-team@contoso.com'

@description('Publisher name shown in API Management.')
param publisherName string = 'Contoso ERP Platform Team'

@description('Application Gateway autoscale minimum. No default: size from your own load tests.')
param appGwMinCapacity int

@description('Application Gateway autoscale maximum. No default: size from your own load tests.')
param appGwMaxCapacity int

@description('WAF mode. Run Detection in non-production while you tune exclusions for JSON-RPC bodies, then use Prevention.')
@allowed([
  'Detection'
  'Prevention'
])
param wafMode string = 'Prevention'

@description('VNet address space.')
param vnetAddressPrefix string = '10.20.0.0/16'

@description('Application Gateway subnet prefix (dedicated subnet).')
param appGwSubnetPrefix string = '10.20.1.0/24'

@description('API Management subnet prefix (dedicated subnet).')
param apimSubnetPrefix string = '10.20.2.0/24'

@description('Private endpoint subnet prefix.')
param peSubnetPrefix string = '10.20.3.0/24'

@description('Create private DNS zones for Key Vault and Event Hubs and link them to the VNet. Set false when a hub provides them.')
param createPrivateDnsZones bool = true

// Identity provider and MCP resource (see ../../mcp-oauth-front-door/README.md)
@description('OpenID Connect discovery URL of the authorization server.')
param idpOpenIdConfigUrl string = 'https://login.contoso.com/.well-known/openid-configuration'

@description('Expected token issuer.')
param idpIssuer string = 'https://login.contoso.com/'

@description('Resource identifier of this MCP server; must equal the token audience and the URL clients call.')
param mcpResourceUri string = 'https://mcp.contoso.com/mcp'

@description('Protected resource metadata URL for this MCP server.')
param mcpPrmUrl string = 'https://mcp.contoso.com/.well-known/oauth-protected-resource/mcp'

@description('Product key for this MCP server. Must match the product names in the tenant directory.')
param mcpProductKey string = 'orders'

@description('API Management base path for the MCP server. Verify the composed client URL in the portal and keep mcpResourceUri identical to it.')
param mcpServerPath string = 'mcp'

// Tenant directory (see policies/fragment-tenant-directory.xml)
@description('Base URL of the tenant directory API, reachable privately from API Management.')
param tenantDirectoryUrl string

@description('App ID URI that the tenant directory API accepts tokens for.')
param tenantDirectoryAppIdUri string

@description('Tenant directory lookup timeout, in seconds. No default.')
param tenantDirectoryTimeout string

@description('Tenant record cache duration, in seconds. No default. Shorter means faster offboarding.')
param tenantCacheSeconds string

// Per-tenant limits for the default tier (see policies/mcp-tenant-pipeline.xml)
@description('Calls per tenant per rate period. No default.')
param tenantRateCalls string

@description('Rate period, in seconds. No default.')
param tenantRatePeriod string

@description('Calls per tenant per quota period. No default.')
param tenantQuotaCalls string

@description('Quota period, in seconds. No default.')
param tenantQuotaPeriod string

// Backends
@description('App ID URI that the MCP adapters in every cell accept tokens for.')
param backendAppIdUri string

@description('MCP adapter URL per cell. Each key becomes the API Management backend ID "cell-<key>".')
param cellBackendUrls object = {
  a: 'https://mcp-adapter.cell-a.erp.contoso.internal/mcp'
  b: 'https://mcp-adapter.cell-b.erp.contoso.internal/mcp'
}

@description('Role definition ID (GUID) of the built-in "Azure Event Hubs Data Sender" role. Look it up with: az role definition list --name "Azure Event Hubs Data Sender" --query "[0].name" -o tsv')
param eventHubsDataSenderRoleId string

@description('Resource tags.')
param tags object = {
  workload: 'multi-tenant-mcp-gateway'
}

// ---------------------------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------------------------

var suffix = uniqueString(resourceGroup().id)
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
var appGwName = 'agw-${namePrefix}'

// ---------------------------------------------------------------------------------------------
// Network
// ---------------------------------------------------------------------------------------------

resource nsgAppGw 'Microsoft.Network/networkSecurityGroups@2023-11-01' = {
  name: 'nsg-appgw-${namePrefix}'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        // Required for Application Gateway v2 infrastructure. Check current docs.
        name: 'allow-gateway-manager'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'GatewayManager'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '65200-65535'
        }
      }
      {
        // Clients reach the MCP endpoint over HTTPS only. Narrow the source if an upstream firewall or NVA fronts this.
        name: 'allow-https-clients'
        properties: {
          priority: 110
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '443'
        }
      }
    ]
  }
}

resource nsgApim 'Microsoft.Network/networkSecurityGroups@2023-11-01' = {
  name: 'nsg-apim-${namePrefix}'
  location: location
  tags: tags
  properties: {
    // Inbound rules for classic internal VNet injection. If you restrict outbound traffic, add the
    // outbound service-tag rules from the API Management VNet reference. Check current docs.
    securityRules: [
      {
        name: 'allow-apim-management'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'ApiManagement'
          sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork'
          destinationPortRange: '3443'
        }
      }
      {
        name: 'allow-azure-load-balancer'
        properties: {
          priority: 110
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'AzureLoadBalancer'
          sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork'
          destinationPortRange: '6390'
        }
      }
      {
        // Only Application Gateway may call the gateway. Tenant identity never depends on this rule.
        name: 'allow-https-from-appgw'
        properties: {
          priority: 120
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: appGwSubnetPrefix
          sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork'
          destinationPortRange: '443'
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: 'vnet-${namePrefix}'
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
        name: 'snet-appgw'
        properties: {
          addressPrefix: appGwSubnetPrefix
          networkSecurityGroup: {
            id: nsgAppGw.id
          }
        }
      }
      {
        name: 'snet-apim'
        properties: {
          addressPrefix: apimSubnetPrefix
          networkSecurityGroup: {
            id: nsgApim.id
          }
        }
      }
      {
        name: 'snet-pe'
        properties: {
          addressPrefix: peSubnetPrefix
          privateEndpointNetworkPolicies: 'Enabled'
        }
      }
    ]
  }
}

var appGwSubnetId = '${vnet.id}/subnets/snet-appgw'
var apimSubnetId = '${vnet.id}/subnets/snet-apim'
var peSubnetId = '${vnet.id}/subnets/snet-pe'

resource dnsKeyVault 'Microsoft.Network/privateDnsZones@2020-06-01' = if (createPrivateDnsZones) {
  name: 'privatelink.vaultcore.azure.net'
  location: 'global'
  tags: tags
}

resource dnsKeyVaultLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = if (createPrivateDnsZones) {
  parent: dnsKeyVault
  name: 'link-${vnet.name}'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

resource dnsEventHubs 'Microsoft.Network/privateDnsZones@2020-06-01' = if (createPrivateDnsZones) {
  name: 'privatelink.servicebus.windows.net'
  location: 'global'
  tags: tags
}

resource dnsEventHubsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = if (createPrivateDnsZones) {
  parent: dnsEventHubs
  name: 'link-${vnet.name}'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

// ---------------------------------------------------------------------------------------------
// Observability: Log Analytics, Application Insights, Event Hubs (audit stream to the SIEM)
// ---------------------------------------------------------------------------------------------

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${namePrefix}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    // Set retention from your audit and privacy policy.
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${namePrefix}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
  }
}

resource ehNamespace 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: 'evhns-${namePrefix}-${suffix}'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
    tier: 'Standard'
  }
  properties: {
    disableLocalAuth: true
    publicNetworkAccess: 'Disabled'
    minimumTlsVersion: '1.2'
  }
}

resource auditHub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: ehNamespace
  name: 'mcp-audit'
  properties: {
    // Set partitions and retention from your audit volume and SIEM ingestion design.
  }
}

resource ehPe 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pe-evhns-${namePrefix}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: peSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'evhns'
        properties: {
          privateLinkServiceId: ehNamespace.id
          groupIds: [
            'namespace'
          ]
        }
      }
    ]
  }
}

resource ehPeDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = if (createPrivateDnsZones) {
  parent: ehPe
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'servicebus'
        properties: {
          privateDnsZoneId: dnsEventHubs.id
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------------------------
// Key Vault: TLS certificate, Application Insights connection string, per-tenant secrets
// ---------------------------------------------------------------------------------------------

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: take('kv-${namePrefix}-${suffix}', 24)
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    enablePurgeProtection: true
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
  }
}

resource kvPe 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pe-kv-${namePrefix}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: peSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'vault'
        properties: {
          privateLinkServiceId: kv.id
          groupIds: [
            'vault'
          ]
        }
      }
    ]
  }
}

resource kvPeDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = if (createPrivateDnsZones) {
  parent: kvPe
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'vault'
        properties: {
          privateDnsZoneId: dnsKeyVault.id
        }
      }
    ]
  }
}

resource kvSecretAppInsights 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: kv
  name: 'appinsights-connection-string'
  properties: {
    value: appInsights.properties.ConnectionString
  }
}

resource appGwIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-appgw-${namePrefix}'
  location: location
  tags: tags
}

// ---------------------------------------------------------------------------------------------
// API Management (internal VNet)
// ---------------------------------------------------------------------------------------------

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: 'apim-${namePrefix}-${suffix}'
  location: location
  tags: tags
  sku: {
    name: apimSkuName
    capacity: apimSkuCapacity
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
    virtualNetworkType: 'Internal'
    virtualNetworkConfiguration: {
      subnetResourceId: apimSubnetId
    }
    // No custom domain on API Management: Application Gateway owns the public host name and
    // overrides the Host header to the gateway's default host name. Check current docs.
    customProperties: {
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Protocols.Tls10': 'false'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Protocols.Tls11': 'false'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Tls10': 'false'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Tls11': 'false'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Ciphers.TripleDes168': 'false'
    }
  }
}

var apimGatewayHost = replace(apim.properties.gatewayUrl, 'https://', '')

resource kvSecretsUserApim 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, apim.id, keyVaultSecretsUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: apim.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource kvSecretsUserAppGw 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, appGwIdentity.id, keyVaultSecretsUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: appGwIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource ehSenderApim 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: auditHub
  name: guid(auditHub.id, apim.id, eventHubsDataSenderRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', eventHubsDataSenderRoleId)
    principalId: apim.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Named values. Plain values carry no secrets. Limits are strings because policies substitute them as text.
var plainNamedValues = [
  { name: 'idp-openid-config-url', value: idpOpenIdConfigUrl }
  { name: 'idp-issuer', value: idpIssuer }
  { name: 'mcp-resource-uri', value: mcpResourceUri }
  { name: 'mcp-prm-url', value: mcpPrmUrl }
  { name: 'mcp-product-key', value: mcpProductKey }
  { name: 'tenant-directory-url', value: tenantDirectoryUrl }
  { name: 'tenant-directory-app-id-uri', value: tenantDirectoryAppIdUri }
  { name: 'tenant-directory-timeout', value: tenantDirectoryTimeout }
  { name: 'tenant-cache-seconds', value: tenantCacheSeconds }
  { name: 'tenant-rate-calls', value: tenantRateCalls }
  { name: 'tenant-rate-period', value: tenantRatePeriod }
  { name: 'tenant-quota-calls', value: tenantQuotaCalls }
  { name: 'tenant-quota-period', value: tenantQuotaPeriod }
  { name: 'backend-app-id-uri', value: backendAppIdUri }
]

resource namedValues 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = [
  for nv in plainNamedValues: {
    parent: apim
    name: nv.name
    properties: {
      displayName: nv.name
      value: nv.value
      secret: false
    }
  }
]

// Key Vault-backed named value. API Management reads it with its managed identity over the private endpoint.
// Add per-tenant backend secrets the same way (one named value per secret, never a shared one).
resource nvAppInsights 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'appinsights-connection-string'
  properties: {
    displayName: 'appinsights-connection-string'
    secret: true
    keyVault: {
      secretIdentifier: kvSecretAppInsights.properties.secretUri
    }
  }
  dependsOn: [
    kvSecretsUserApim
    kvPeDns
  ]
}

resource aiLogger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = {
  parent: apim
  name: 'appinsights'
  properties: {
    loggerType: 'applicationInsights'
    description: 'Gateway telemetry and per-tenant custom metrics.'
    resourceId: appInsights.id
    credentials: {
      connectionString: '{{appinsights-connection-string}}'
    }
  }
  dependsOn: [
    nvAppInsights
  ]
}

resource auditLogger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = {
  parent: apim
  name: 'audit-eventhub'
  properties: {
    loggerType: 'azureEventHub'
    description: 'Per-call MCP audit events. No bodies, arguments, results, or tokens.'
    credentials: {
      endpointAddress: '${ehNamespace.name}.servicebus.windows.net'
      identityClientId: 'SystemAssigned'
      name: auditHub.name
    }
  }
  dependsOn: [
    ehSenderApim
    ehPeDns
  ]
}

// Service-wide diagnostics. Body logging stays off: reading response bodies breaks MCP streaming,
// and request bodies carry tool arguments. Enable custom metrics with dimensions in Application
// Insights so emit-metric dimensions are kept. Check current docs.
resource apimDiagnostics 'Microsoft.ApiManagement/service/diagnostics@2024-05-01' = {
  parent: apim
  name: 'applicationinsights'
  properties: {
    loggerId: aiLogger.id
    alwaysLog: 'allErrors'
    httpCorrelationProtocol: 'W3C'
    verbosity: 'information'
    metrics: true
    frontend: {
      request: {
        body: {
          bytes: 0
        }
      }
      response: {
        body: {
          bytes: 0
        }
      }
    }
    backend: {
      request: {
        body: {
          bytes: 0
        }
      }
      response: {
        body: {
          bytes: 0
        }
      }
    }
  }
}

resource apimLogsToLaw 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: apim
  name: 'to-log-analytics'
  properties: {
    workspaceId: law.id
    logAnalyticsDestinationType: 'Dedicated'
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

// One registered backend per cell. Policies select "cell-<cell>"; they never build URLs from tenant data.
resource cellBackends 'Microsoft.ApiManagement/service/backends@2024-05-01' = [
  for cell in items(cellBackendUrls): {
    parent: apim
    name: 'cell-${cell.key}'
    properties: {
      description: 'MCP adapter for cell ${cell.key}'
      protocol: 'http'
      url: cell.value
      tls: {
        validateCertificateChain: true
        validateCertificateName: true
      }
      // Add circuitBreaker rules so one unhealthy cell fails fast. Thresholds come from your own SLOs.
    }
  }
]

resource fragTenantDirectory 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'tenant-directory'
  properties: {
    description: 'Resolve the tenant record from the tenant key taken from the validated token.'
    format: 'rawxml'
    value: loadTextContent('policies/fragment-tenant-directory.xml')
  }
  dependsOn: [
    namedValues
  ]
}

resource fragAudit 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = {
  parent: apim
  name: 'mcp-audit'
  properties: {
    description: 'Emit the per-tenant metric and the audit event for every outcome.'
    format: 'rawxml'
    value: loadTextContent('policies/fragment-mcp-audit.xml')
  }
  dependsOn: [
    auditLogger
    apimDiagnostics
  ]
}

resource globalPolicy 'Microsoft.ApiManagement/service/policies@2024-05-01' = {
  parent: apim
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: loadTextContent('policies/global.xml')
  }
}

// MCP server that passes through to the MCP adapter in each cell.
// Preview API version and property shape: verify against the current ARM reference before use.
#disable-next-line BCP081
resource mcpServer 'Microsoft.ApiManagement/service/apis@2025-09-01-preview' = {
  parent: apim
  name: 'contoso-erp-orders-mcp'
  properties: {
    displayName: 'Contoso ERP orders (MCP)'
    description: 'Orders tools for tenant agents. Entitlement is enforced per tenant on every call.'
    type: 'mcp'
    path: mcpServerPath
    protocols: [
      'https'
    ]
    subscriptionRequired: false
    serviceUrl: cellBackendUrls.a
    mcpProperties: {
      transportType: 'streamable'
      endpoints: [
        {
          name: 'message'
          uriTemplate: '/mcp'
        }
      ]
    }
  }
}

#disable-next-line BCP081
resource mcpServerPolicy 'Microsoft.ApiManagement/service/apis/policies@2025-09-01-preview' = {
  parent: mcpServer
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: loadTextContent('policies/mcp-tenant-pipeline.xml')
  }
  dependsOn: [
    namedValues
    fragTenantDirectory
    fragAudit
    cellBackends
    globalPolicy
  ]
}

// Product = commercial bundle. Mirror the same key in the tenant directory. Access is granted by the
// tenant directory and the token, not by a subscription key, so subscriptionRequired is false.
resource product 'Microsoft.ApiManagement/service/products@2024-05-01' = {
  parent: apim
  name: mcpProductKey
  properties: {
    displayName: 'Contoso ERP ${mcpProductKey}'
    description: 'MCP tools for the ${mcpProductKey} product.'
    state: 'published'
    subscriptionRequired: false
  }
}

resource productMcpServer 'Microsoft.ApiManagement/service/products/apis@2024-05-01' = {
  parent: product
  name: mcpServer.name
}

// ---------------------------------------------------------------------------------------------
// Edge: WAF policy and Application Gateway WAF_v2
// ---------------------------------------------------------------------------------------------

resource wafPolicy 'Microsoft.Network/ApplicationGatewayWebApplicationFirewallPolicies@2023-11-01' = {
  name: 'waf-${namePrefix}'
  location: location
  tags: tags
  properties: {
    policySettings: {
      state: 'Enabled'
      mode: wafMode
      requestBodyCheck: true
      // Set request body and file size limits deliberately for JSON-RPC payloads. Check current docs.
    }
    managedRules: {
      managedRuleSets: [
        {
          ruleSetType: 'Microsoft_DefaultRuleSet'
          ruleSetVersion: '2.1'
        }
      ]
      // Add narrowly scoped exclusions (for example a single rule on a single JSON field) only after
      // reviewing Detection-mode logs. Never disable body inspection for the MCP path as a shortcut.
    }
  }
}

resource appGwPip 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: 'pip-agw-${namePrefix}'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource appGw 'Microsoft.Network/applicationGateways@2023-11-01' = {
  name: appGwName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${appGwIdentity.id}': {}
    }
  }
  properties: {
    sku: {
      name: 'WAF_v2'
      tier: 'WAF_v2'
    }
    autoscaleConfiguration: {
      minCapacity: appGwMinCapacity
      maxCapacity: appGwMaxCapacity
    }
    firewallPolicy: {
      id: wafPolicy.id
    }
    // WAF needs request buffering. Response buffering stays off so SSE events flow as they are produced.
    globalConfiguration: {
      enableRequestBuffering: true
      enableResponseBuffering: false
    }
    gatewayIPConfigurations: [
      {
        name: 'gw-ipconfig'
        properties: {
          subnet: {
            id: appGwSubnetId
          }
        }
      }
    ]
    frontendIPConfigurations: [
      {
        name: 'fe-public'
        properties: {
          publicIPAddress: {
            id: appGwPip.id
          }
        }
      }
    ]
    frontendPorts: [
      {
        name: 'port-https'
        properties: {
          port: 443
        }
      }
    ]
    sslCertificates: [
      {
        name: 'mcp-tls'
        properties: {
          keyVaultSecretId: tlsCertificateKeyVaultUri
        }
      }
    ]
    backendAddressPools: [
      {
        name: 'pool-apim'
        properties: {
          backendAddresses: [
            {
              ipAddress: apim.properties.privateIPAddresses[0]
            }
          ]
        }
      }
    ]
    probes: [
      {
        name: 'probe-apim'
        properties: {
          protocol: 'Https'
          path: '/status-0123456789abcdef'
          pickHostNameFromBackendHttpSettings: true
          match: {
            statusCodes: [
              '200-399'
            ]
          }
          // Set interval, timeout, and unhealthy threshold from your own health objectives.
        }
      }
    ]
    backendHttpSettingsCollection: [
      {
        name: 'https-apim'
        properties: {
          port: 443
          protocol: 'Https'
          // Override Host to the API Management default gateway host so its certificate validates.
          hostName: apimGatewayHost
          pickHostNameFromBackendAddress: false
          probe: {
            id: resourceId('Microsoft.Network/applicationGateways/probes', appGwName, 'probe-apim')
          }
          // Tune requestTimeout for long-lived Streamable HTTP and SSE responses. Check current docs.
        }
      }
    ]
    httpListeners: [
      {
        name: 'listener-mcp'
        properties: {
          protocol: 'Https'
          hostName: mcpHostName
          requireServerNameIndication: true
          frontendIPConfiguration: {
            id: resourceId('Microsoft.Network/applicationGateways/frontendIPConfigurations', appGwName, 'fe-public')
          }
          frontendPort: {
            id: resourceId('Microsoft.Network/applicationGateways/frontendPorts', appGwName, 'port-https')
          }
          sslCertificate: {
            id: resourceId('Microsoft.Network/applicationGateways/sslCertificates', appGwName, 'mcp-tls')
          }
        }
      }
    ]
    // Defense in depth: drop client-supplied tenant hints at the edge. API Management deletes them again.
    // An empty header value removes the header.
    rewriteRuleSets: [
      {
        name: 'strip-tenant-hints'
        properties: {
          rewriteRules: [
            {
              name: 'remove-client-tenant-headers'
              ruleSequence: 100
              actionSet: {
                requestHeaderConfigurations: [
                  {
                    headerName: 'X-Tenant-Id'
                    headerValue: ''
                  }
                  {
                    headerName: 'X-Contoso-Gateway-Tenant'
                    headerValue: ''
                  }
                  {
                    headerName: 'X-Contoso-User-Assertion'
                    headerValue: ''
                  }
                ]
              }
            }
          ]
        }
      }
    ]
    requestRoutingRules: [
      {
        name: 'rule-mcp'
        properties: {
          ruleType: 'Basic'
          priority: 100
          httpListener: {
            id: resourceId('Microsoft.Network/applicationGateways/httpListeners', appGwName, 'listener-mcp')
          }
          backendAddressPool: {
            id: resourceId('Microsoft.Network/applicationGateways/backendAddressPools', appGwName, 'pool-apim')
          }
          backendHttpSettings: {
            id: resourceId('Microsoft.Network/applicationGateways/backendHttpSettingsCollection', appGwName, 'https-apim')
          }
          rewriteRuleSet: {
            id: resourceId('Microsoft.Network/applicationGateways/rewriteRuleSets', appGwName, 'strip-tenant-hints')
          }
        }
      }
    ]
  }
  dependsOn: [
    kvSecretsUserAppGw
    kvPeDns
  ]
}

resource appGwLogsToLaw 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: appGw
  name: 'to-log-analytics'
  properties: {
    workspaceId: law.id
    logAnalyticsDestinationType: 'Dedicated'
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
  }
}

// ---------------------------------------------------------------------------------------------
// API Center: design-time registry for MCP servers
// ---------------------------------------------------------------------------------------------

resource apiCenter 'Microsoft.ApiCenter/services@2024-03-01' = {
  name: 'apic-${namePrefix}-${suffix}'
  location: apiCenterLocation
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {}
}

resource apiCenterWorkspace 'Microsoft.ApiCenter/services/workspaces@2024-03-01' existing = {
  parent: apiCenter
  name: 'default'
}

// Register MCP servers, versions, and owners in this environment (portal, CLI, or API Management sync).
// MCP server registration in API Center is new. Check current docs for the supported API kinds.
resource apiCenterEnvironment 'Microsoft.ApiCenter/services/workspaces/environments@2024-03-01' = {
  parent: apiCenterWorkspace
  name: 'mcp-gateway-production'
  properties: {
    title: 'Contoso ERP MCP gateway (production)'
    description: 'Multi-tenant MCP front door on API Management.'
    kind: 'production'
    server: {
      type: 'Azure API Management'
      managementPortalUri: [
        'https://portal.azure.com/#resource${apim.id}'
      ]
    }
  }
}

// ---------------------------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------------------------

output apimName string = apim.name
output apimPrivateIp string = apim.properties.privateIPAddresses[0]
output appGatewayPublicIp string = appGwPip.properties.ipAddress
output apiCenterName string = apiCenter.name
output keyVaultName string = kv.name
output auditEventHub string = '${ehNamespace.name}/${auditHub.name}'
output mcpEndpoint string = 'https://${mcpHostName}/mcp'
