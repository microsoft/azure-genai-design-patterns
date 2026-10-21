// ILLUSTRATIVE AND UNDEPLOYED.
//
// Streaming model gateway for Foundry hosted agents. Deploys the gateway's own
// identity, telemetry, Key Vault and Container Apps environment, grants the
// identity the narrow roles it needs on an existing Foundry account and
// registry, and (in a second pass) the gateway container app itself.
//
// Run it twice, as described in docs/deployment-and-operations.md:
//   Phase 2 (foundation): deployGatewayApp = false
//   Phase 5 (app):        deployGatewayApp = true, plus gatewayImage (by
//                         digest) and gatewayPolicyJson
//
// What it does:
//   * Creates one user-assigned managed identity for the gateway and uses it
//     for the image pull, the Key Vault reference and the Foundry calls.
//   * Creates Log Analytics and workspace-based Application Insights, and
//     routes environment and Key Vault logs to the workspace without shared
//     keys in the template.
//   * Creates Key Vault in RBAC mode with purge protection, public network
//     access disabled by default and an optional private endpoint.
//   * Creates a Container Apps environment (workload profiles), joined to
//     your subnet and internal unless you deliberately choose otherwise.
//   * Assigns the model inference role on the Foundry account, Key Vault
//     Secrets User on the vault and AcrPull on the registry, each at the
//     narrowest scope.
//   * Deploys the gateway app in multiple-revision mode, with every timeout,
//     replica bound and concurrency target taken from required parameters.
//
// What it does not do:
//   * Create the Foundry account, its deployments, the registry, the network
//     or private DNS. Those come from the landing zone.
//   * Create the Entra app registration or assign the Gateway.Invoke app role.
//   * Put any secret value in a parameter. The bring-your-own key is seeded
//     into Key Vault out of band and read through a Key Vault reference.
//   * Guess role definition IDs that are not well-known built-ins, or choose
//     timeouts and capacity for you.

targetScope = 'resourceGroup'

// ---------------------------------------------------------------------------
// Parameters: naming and placement
// ---------------------------------------------------------------------------

@description('Azure region for the gateway resources.')
param location string = resourceGroup().location

@description('Short name prefix for every resource this template creates, for example contoso-model-gw.')
@minLength(3)
@maxLength(20)
param namePrefix string

@description('Tags applied to every resource this template creates.')
param tags object = {}

// ---------------------------------------------------------------------------
// Parameters: existing dependencies from the landing zone
// ---------------------------------------------------------------------------

@description('Name of the existing Foundry account the gateway calls. It must have local (key) authentication disabled.')
@minLength(1)
param foundryAccountName string

@description('Resource group of the Foundry account.')
param foundryResourceGroupName string = resourceGroup().name

@description('Optional second Foundry account in another region, used as a failover backend. Leave empty for none.')
param foundrySecondaryAccountName string = ''

@description('Resource group of the optional second Foundry account.')
param foundrySecondaryResourceGroupName string = resourceGroup().name

@description('Name of the existing private container registry that holds the gateway image.')
@minLength(1)
param containerRegistryName string

@description('Resource group of the container registry.')
param containerRegistryResourceGroupName string = resourceGroup().name

// ---------------------------------------------------------------------------
// Parameters: network
// ---------------------------------------------------------------------------

@description('When false (recommended), the Container Apps environment is internal: the gateway is reachable only from your network. Requires infrastructureSubnetId.')
param ingressExternal bool = false

@description('Resource ID of a subnet delegated to Microsoft.App/environments. Required when ingressExternal is false; the deployment fails if it is missing.')
param infrastructureSubnetId string = ''

@description('Optional IP allow rules for the gateway ingress, in the Container Apps ipSecurityRestrictions shape. An internal environment and network security groups are the primary control.')
param ipSecurityRestrictions array = []

@description('Key Vault public network access. Keep Disabled and use the private endpoint unless you deliberately choose otherwise.')
@allowed([
  'Disabled'
  'Enabled'
])
param keyVaultPublicNetworkAccess string = 'Disabled'

@description('Optional subnet for the Key Vault private endpoint. Leave empty to skip the private endpoint.')
param privateEndpointSubnetId string = ''

@description('Optional resource ID of the existing privatelink.vaultcore.azure.net private DNS zone from the landing zone.')
param keyVaultPrivateDnsZoneId string = ''

// ---------------------------------------------------------------------------
// Parameters: gateway app (phase 5)
// ---------------------------------------------------------------------------

@description('Phase switch. false deploys the foundation only (phase 2); true also deploys the gateway app (phase 5).')
param deployGatewayApp bool = false

@description('Gateway image by digest, for example <registry>.azurecr.io/model-gateway@sha256:<digest>. Required when deployGatewayApp is true.')
param gatewayImage string = ''

@description('Gateway policy document (callers, routes, budget window) as a JSON string. Contains no secrets. Changing it creates a new revision. The gateway refuses to start if it is empty or inconsistent.')
param gatewayPolicyJson string = ''

@description('Entra tenant ID the gateway accepts tokens from. Exactly one tenant.')
@minLength(1)
param gatewayTenantId string

@description('Accepted token audiences: the gateway app ID URI and its client ID, so either token version is accepted.')
@minLength(1)
param gatewayAudiences array

@description('App role a caller token must carry.')
param gatewayRequiredRole string = 'Gateway.Invoke'

@description('Interval between SSE keepalive comments. Must be below the shortest idle timeout on the path. No default: state it per environment.')
@minValue(1)
param keepaliveSeconds int

@description('How long the gateway waits for a backend\'s first event before failing over. No default.')
@minValue(1)
param firstByteTimeoutSeconds int

@description('Maximum backend attempts per request, all before the first byte. No default.')
@minValue(1)
param maxAttempts int

@description('Shortest idle timeout of any layer between agent and gateway (ingress, proxies). The gateway checks its timeouts against it at startup. No default.')
@minValue(1)
param ingressIdleTimeoutSeconds int

@description('false (default) holds response headers until the first backend event (hold-headers mode). true sends headers immediately and keepalives while waiting (early-commit mode).')
param earlyCommit bool = false

@description('Container CPU cores as a string, for example the value your sizing tests chose. No default.')
@minLength(1)
param containerCpu string

@description('Container memory, for example a value with a Gi suffix from your sizing tests. No default.')
@minLength(1)
param containerMemory string

@description('Minimum replicas. Keep above zero so the first request does not pay a cold start. No default.')
@minValue(1)
param minReplicas int

@description('Maximum replicas. Keep at the minimum until budgets and idempotency use a shared store. No default.')
@minValue(1)
param maxReplicas int

@description('Concurrent requests per replica that triggers scale-out. Streams are long-lived, so scale on concurrency, not CPU. No default.')
@minValue(1)
param concurrentRequests int

@description('Grace period for in-flight streams when a replica stops (scale-in, revision deactivation). No default.')
@minValue(1)
param terminationGracePeriodSeconds int

@description('Revision suffix for this deployment, for example a short commit ID. Required when stableRevisionName is set.')
param revisionSuffix string = ''

@description('Name of the revision that currently serves traffic. Leave empty on the first app deployment; set it to test a candidate revision with no traffic.')
param stableRevisionName string = ''

@description('Traffic label for the candidate revision; gives it its own test address.')
param candidateLabel string = 'candidate'

@description('Share of traffic (percent) for the candidate revision while stableRevisionName is set.')
@minValue(0)
@maxValue(100)
param candidateWeight int = 0

// ---------------------------------------------------------------------------
// Parameters: bring-your-own backend (optional)
// ---------------------------------------------------------------------------

@description('Name of the Key Vault secret that holds the bring-your-own provider key. The value is seeded out of band. Leave empty for no bring-your-own route.')
param byoBackendSecretName string = ''

@description('Base URL of the bring-your-own OpenAI-compatible provider. Leave empty for no bring-your-own route.')
param byoBackendUrl string = ''

// The linter flags any parameter name containing "secret". These are object
// IDs and role IDs, not secret values.
@description('Optional principal that seeds the bring-your-own secret (Key Vault Secrets Officer). Grant just in time and remove afterwards.')
#disable-next-line secure-secrets-in-params
param secretSeederPrincipalId string = ''

@description('Principal type of the secret seeder.')
@allowed([
  'User'
  'Group'
  'ServicePrincipal'
])
#disable-next-line secure-secrets-in-params
param secretSeederPrincipalType string = 'ServicePrincipal'

// ---------------------------------------------------------------------------
// Parameters: telemetry and roles
// ---------------------------------------------------------------------------

@description('Log Analytics retention in days. Set it from your audit and cost-reporting obligations. No default.')
@minValue(1)
param logRetentionDays int

// Not a well-known ID in this repository: look it up in your tenant, for
// example Cognitive Services OpenAI User or Foundry User. The template never
// guesses an ID.
@description('Role definition ID of the model inference role granted to the gateway on the Foundry account. Required.')
@minLength(36)
@maxLength(36)
param modelInferenceRoleDefinitionId string

@description('Role definition ID of Key Vault Secrets User (well-known built-in role).')
#disable-next-line secure-secrets-in-params
param keyVaultSecretsUserRoleDefinitionId string = '4633458b-17de-408a-b874-0445c86b69e6'

@description('Role definition ID of Key Vault Secrets Officer (well-known built-in role), used only for the optional secret seeder.')
#disable-next-line secure-secrets-in-params
param keyVaultSecretsOfficerRoleDefinitionId string = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'

@description('Role definition ID of AcrPull (well-known built-in role).')
param acrPullRoleDefinitionId string = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

// ---------------------------------------------------------------------------
// Variables
// ---------------------------------------------------------------------------

var workspaceName = '${namePrefix}-law'
var insightsName = '${namePrefix}-appi'
var identityName = '${namePrefix}-gw-id'
var environmentName = '${namePrefix}-env'
var gatewayAppName = '${namePrefix}-gw'
var keyVaultName = take('kv${replace(namePrefix, '-', '')}${uniqueString(resourceGroup().id)}', 24)

var useSecondary = !empty(foundrySecondaryAccountName)
var useByo = !empty(byoBackendSecretName) && !empty(byoBackendUrl)
var useKeyVaultPrivateEndpoint = !empty(privateEndpointSubnetId)

// An internal environment must be joined to a subnet. Leaving the subnet empty
// with ingressExternal = false sends an empty subnet ID, and the deployment
// fails rather than silently creating a public environment.
var joinNetwork = !ingressExternal || !empty(infrastructureSubnetId)

var gatewayPort = 8080

// Blue-green traffic: the first deployment sends everything to the latest
// revision. Later deployments name the stable revision, create the candidate
// with a known suffix and a label, and move traffic with candidateWeight.
var candidateRevisionName = '${gatewayAppName}--${revisionSuffix}'
var traffic = empty(stableRevisionName)
  ? [
      {
        latestRevision: true
        weight: 100
      }
    ]
  : [
      {
        revisionName: stableRevisionName
        label: 'stable'
        weight: 100 - candidateWeight
      }
      {
        revisionName: candidateRevisionName
        label: candidateLabel
        weight: candidateWeight
      }
    ]

// ---------------------------------------------------------------------------
// Identity and telemetry
// ---------------------------------------------------------------------------

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: tags
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: logRetentionDays
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: insightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
  }
}

// ---------------------------------------------------------------------------
// Key Vault for the bring-your-own backend key
// ---------------------------------------------------------------------------

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    // RBAC, not access policies: each identity gets exactly one data role.
    enableRbacAuthorization: true
    enableSoftDelete: true
    enablePurgeProtection: true
    publicNetworkAccess: keyVaultPublicNetworkAccess
    networkAcls: {
      bypass: 'None'
      defaultAction: 'Deny'
    }
  }
}

resource keyVaultDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: keyVault
  name: 'to-workspace'
  properties: {
    workspaceId: workspace.id
    logs: [
      {
        categoryGroup: 'audit'
        enabled: true
      }
    ]
  }
}

resource keyVaultPrivateEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = if (useKeyVaultPrivateEndpoint) {
  name: '${namePrefix}-kv-pe'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'key-vault'
        properties: {
          privateLinkServiceId: keyVault.id
          groupIds: [
            'vault'
          ]
        }
      }
    ]
  }
}

resource keyVaultDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = if (useKeyVaultPrivateEndpoint && !empty(keyVaultPrivateDnsZoneId)) {
  parent: keyVaultPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'key-vault'
        properties: {
          privateDnsZoneId: keyVaultPrivateDnsZoneId
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Role assignments: the gateway identity is the only model boundary
// ---------------------------------------------------------------------------

module foundryRole 'modules/foundry-model-role.bicep' = {
  name: '${namePrefix}-foundry-role'
  scope: resourceGroup(foundryResourceGroupName)
  params: {
    foundryAccountName: foundryAccountName
    principalId: identity.properties.principalId
    roleDefinitionId: modelInferenceRoleDefinitionId
  }
}

module foundrySecondaryRole 'modules/foundry-model-role.bicep' = if (useSecondary) {
  name: '${namePrefix}-foundry2-role'
  scope: resourceGroup(foundrySecondaryResourceGroupName)
  params: {
    foundryAccountName: foundrySecondaryAccountName
    principalId: identity.properties.principalId
    roleDefinitionId: modelInferenceRoleDefinitionId
  }
}

module registryRole 'modules/registry-pull-role.bicep' = {
  name: '${namePrefix}-acr-role'
  scope: resourceGroup(containerRegistryResourceGroupName)
  params: {
    registryName: containerRegistryName
    principalId: identity.properties.principalId
    acrPullRoleDefinitionId: acrPullRoleDefinitionId
  }
}

resource secretsUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, identity.id, keyVaultSecretsUserRoleDefinitionId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleDefinitionId)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource secretSeederRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(secretSeederPrincipalId)) {
  scope: keyVault
  name: guid(keyVault.id, secretSeederPrincipalId, keyVaultSecretsOfficerRoleDefinitionId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsOfficerRoleDefinitionId)
    principalId: secretSeederPrincipalId
    principalType: secretSeederPrincipalType
  }
}

// ---------------------------------------------------------------------------
// Container Apps environment
// ---------------------------------------------------------------------------

// With an internal environment, the app's ingress setting "external" means
// "reachable from the environment's network", not "reachable from the
// internet". Publish the default domain through a private DNS zone with a
// wildcard record to environmentStaticIp (the landing zone does this).
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'azure-monitor'
    }
    vnetConfiguration: joinNetwork
      ? {
          infrastructureSubnetId: infrastructureSubnetId
          internal: !ingressExternal
        }
      : null
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource environmentDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: environment
  name: 'to-workspace'
  properties: {
    workspaceId: workspace.id
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Gateway container app (phase 5)
// ---------------------------------------------------------------------------

var baseSecrets = [
  {
    name: 'appinsights-connection-string'
    value: insights.properties.ConnectionString
  }
]

// Versionless secret URL, so a rotated version is picked up on the next
// refresh or revision restart.
var byoSecrets = useByo
  ? [
      {
        name: 'byo-backend-api-key'
        keyVaultUrl: '${keyVault.properties.vaultUri}secrets/${byoBackendSecretName}'
        identity: identity.id
      }
    ]
  : []

var baseEnv = [
  {
    name: 'AZURE_CLIENT_ID'
    value: identity.properties.clientId
  }
  {
    name: 'GATEWAY_TENANT_ID'
    value: gatewayTenantId
  }
  {
    name: 'GATEWAY_AUDIENCES'
    value: join(gatewayAudiences, ',')
  }
  {
    name: 'GATEWAY_REQUIRED_ROLE'
    value: gatewayRequiredRole
  }
  {
    // A plain setting, not a secret: a policy change must create a revision.
    name: 'GATEWAY_POLICY_JSON'
    value: gatewayPolicyJson
  }
  {
    name: 'GATEWAY_KEEPALIVE_SECONDS'
    value: string(keepaliveSeconds)
  }
  {
    name: 'GATEWAY_FIRST_BYTE_TIMEOUT_SECONDS'
    value: string(firstByteTimeoutSeconds)
  }
  {
    name: 'GATEWAY_MAX_ATTEMPTS'
    value: string(maxAttempts)
  }
  {
    name: 'GATEWAY_INGRESS_IDLE_TIMEOUT_SECONDS'
    value: string(ingressIdleTimeoutSeconds)
  }
  {
    name: 'GATEWAY_EARLY_COMMIT'
    value: earlyCommit ? 'true' : 'false'
  }
  {
    name: 'BACKEND_FOUNDRY_URL'
    value: foundryRole.outputs.openAiV1BaseUrl
  }
  {
    name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
    secretRef: 'appinsights-connection-string'
  }
]

var secondaryEnv = useSecondary
  ? [
      {
        name: 'BACKEND_FOUNDRY_SECONDARY_URL'
        value: foundrySecondaryRole!.outputs.openAiV1BaseUrl
      }
    ]
  : []

var byoEnv = useByo
  ? [
      {
        name: 'BACKEND_BYO_URL'
        value: byoBackendUrl
      }
      {
        name: 'BYO_BACKEND_API_KEY'
        secretRef: 'byo-backend-api-key'
      }
    ]
  : []

resource gateway 'Microsoft.App/containerApps@2024-03-01' = if (deployGatewayApp) {
  name: gatewayAppName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      // Every change (image, policy, timeouts) becomes a revision you can
      // test on its label before it takes traffic.
      activeRevisionsMode: 'Multiple'
      ingress: {
        // See the note on the environment: with an internal environment this
        // is reachable only from your network.
        external: true
        targetPort: gatewayPort
        transport: 'auto'
        allowInsecure: false
        ipSecurityRestrictions: ipSecurityRestrictions
        traffic: traffic
      }
      registries: [
        {
          server: registryRole.outputs.loginServer
          identity: identity.id
        }
      ]
      secrets: concat(baseSecrets, byoSecrets)
    }
    template: {
      revisionSuffix: empty(revisionSuffix) ? null : revisionSuffix
      terminationGracePeriodSeconds: terminationGracePeriodSeconds
      containers: [
        {
          name: 'gateway'
          image: gatewayImage
          resources: {
            cpu: json(containerCpu)
            memory: containerMemory
          }
          env: concat(baseEnv, secondaryEnv, byoEnv)
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: gatewayPort
              }
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/healthz'
                port: gatewayPort
              }
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: string(concurrentRequests)
              }
            }
          }
        ]
      }
    }
  }
  // The Foundry and registry roles are implicit dependencies through module
  // outputs. The Key Vault role is not, so name it: the first revision must
  // be able to read its secret.
  dependsOn: [
    secretsUserRole
  ]
}

// ---------------------------------------------------------------------------
// Outputs (no secrets)
// ---------------------------------------------------------------------------

@description('Ingress host name of the gateway. Empty until the app is deployed.')
output gatewayFqdn string = deployGatewayApp ? gateway!.properties.configuration.ingress.fqdn : ''

@description('Name of the latest gateway revision. Empty until the app is deployed.')
output latestRevisionName string = deployGatewayApp ? gateway!.properties.latestRevisionName : ''

output gatewayIdentityPrincipalId string = identity.properties.principalId
output gatewayIdentityClientId string = identity.properties.clientId
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
output environmentId string = environment.id
output environmentDefaultDomain string = environment.properties.defaultDomain

@description('Static IP of the environment. With an internal environment, point the private DNS wildcard record for environmentDefaultDomain here.')
output environmentStaticIp string = environment.properties.staticIp

@description('True only if every Foundry account the gateway calls has key authentication disabled. The pipeline stops when it is false.')
output foundryLocalAuthDisabled bool = foundryRole.outputs.localAuthDisabled && (useSecondary ? foundrySecondaryRole!.outputs.localAuthDisabled : true)
