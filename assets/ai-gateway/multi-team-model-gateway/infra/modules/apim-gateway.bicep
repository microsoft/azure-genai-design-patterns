// ILLUSTRATIVE AND UNDEPLOYED - validated with `az bicep build` only.
//
// Azure API Management as the AI gateway in front of the shared Foundry account:
//   - system-assigned managed identity (granted a model-inference role by role-assignment.bicep),
//   - one backend pointing at the account's Azure OpenAI endpoint,
//   - one Azure OpenAI-compatible API with explicit operations and an API-scope policy that rejects
//     calls without a team product, enforces the deployment allow-list, strips caller credentials and
//     authenticates to the backend with the managed identity,
//   - optional observability: Log Analytics, Application Insights, an APIM logger, API diagnostics with
//     custom metrics (needed by the token metric policy) and resource logs.
// The per-team token limit itself lives in the product policy (team.bicep, policies/team-token-limit.xml).

@description('Azure region.')
param location string

@description('Short prefix used to derive observability resource names.')
param namePrefix string

@description('API Management service name.')
param apimName string

@description('API Management SKU.')
@allowed([
  'BasicV2'
  'StandardV2'
])
param apimSku string = 'BasicV2'

@description('Publisher email.')
param publisherEmail string

@description('Publisher name.')
param publisherName string

@description('Existing Foundry account in this resource group that hosts the shared deployment.')
param foundryAccountName string

@description('Path prefix of the gateway API; the API is published at <apiPathPrefix>/openai.')
param apiPathPrefix string

@description('Model deployments exposed through the gateway.')
@minLength(1)
param allowedModelDeployments string[]

@description('Deploy observability resources and the token metric policy.')
param enableObservability bool = true

@description('Tags.')
param tags object = {}

var backendId = 'foundry-openai'
var apiName = 'foundry-openai'
var suffix = uniqueString(resourceGroup().id, apimName)

var operations = [
  {
    name: 'chat-completions'
    displayName: 'Create chat completion'
    urlTemplate: '/deployments/{deployment-id}/chat/completions'
  }
  {
    name: 'embeddings'
    displayName: 'Create embeddings'
    urlTemplate: '/deployments/{deployment-id}/embeddings'
  }
]

var tokenMetricFragment = enableObservability ? loadTextContent('../policies/token-metric-fragment.xml') : ''
var apiPolicyXml = replace(
  loadTextContent('../policies/api-gateway-policy.xml'),
  '<!-- TOKEN_METRIC_POLICY -->',
  tokenMetricFragment
)

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: foundryAccountName
}

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: apimName
  location: location
  sku: {
    name: apimSku
    capacity: 1
  }
  identity: {
    type: 'SystemAssigned'
  }
  tags: tags
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
  }
}

resource backend 'Microsoft.ApiManagement/service/backends@2024-05-01' = {
  parent: apim
  name: backendId
  properties: {
    description: 'Shared Foundry account (Azure OpenAI endpoint). Authenticated with the gateway managed identity.'
    protocol: 'http'
    url: 'https://${foundryAccount.properties.customSubDomainName}.openai.azure.com/openai'
    tls: {
      validateCertificateChain: true
      validateCertificateName: true
    }
  }
}

resource allowedDeployments 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'allowed-model-deployments'
  properties: {
    displayName: 'allowed-model-deployments'
    value: join(allowedModelDeployments, ',')
    secret: false
  }
}

resource api 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: apiName
  properties: {
    displayName: 'Shared model deployment (per-team quotas)'
    description: 'Azure OpenAI-compatible surface over the shared Foundry deployment. Callers must use a team product subscription key.'
    path: '${apiPathPrefix}/openai'
    protocols: [
      'https'
    ]
    type: 'http'
    subscriptionRequired: true
    subscriptionKeyParameterNames: {
      header: 'api-key'
      query: 'api-key'
    }
  }
}

resource apiOperations 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [
  for op in operations: {
    parent: api
    name: op.name
    properties: {
      displayName: op.displayName
      method: 'POST'
      urlTemplate: op.urlTemplate
      templateParameters: [
        {
          name: 'deployment-id'
          type: 'string'
          required: true
        }
      ]
      request: {
        queryParameters: [
          {
            name: 'api-version'
            type: 'string'
            required: true
          }
        ]
      }
      responses: []
    }
  }
]

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = if (enableObservability) {
  name: 'log-${namePrefix}-aigw-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = if (enableObservability) {
  name: 'appi-${namePrefix}-aigw-${suffix}'
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

resource apimLogger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = if (enableObservability) {
  parent: apim
  name: 'appinsights'
  properties: {
    loggerType: 'applicationInsights'
    description: 'Per-team token metrics and request telemetry.'
    resourceId: appInsights.id
    credentials: {
      instrumentationKey: appInsights!.properties.InstrumentationKey
    }
  }
}

// Custom metrics must be enabled for the token metric policy. Bodies are never logged (prompts and
// completions stay out of telemetry); the diagnostic x-team header and the quota headers are logged.
resource apiDiagnostics 'Microsoft.ApiManagement/service/apis/diagnostics@2024-05-01' = if (enableObservability) {
  parent: api
  name: 'applicationinsights'
  properties: {
    loggerId: apimLogger.id
    alwaysLog: 'allErrors'
    metrics: true
    verbosity: 'information'
    logClientIp: false
    httpCorrelationProtocol: 'W3C'
    sampling: {
      samplingType: 'fixed'
      percentage: 100
    }
    frontend: {
      request: {
        headers: [
          'x-team'
        ]
        body: {
          bytes: 0
        }
      }
      response: {
        headers: [
          'retry-after'
          'x-remaining-tokens'
          'x-tokens-consumed'
        ]
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

resource apiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: api
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: apiPolicyXml
  }
  dependsOn: [
    backend
    allowedDeployments
    apiOperations
    apiDiagnostics
  ]
}

resource apimResourceLogs 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (enableObservability) {
  name: 'apim-to-log-analytics'
  scope: apim
  properties: {
    workspaceId: logAnalytics.id
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

@description('API Management service name.')
output apimName string = apim.name

@description('API Management resource ID (stored in each team connection metadata).')
output apimId string = apim.id

@description('APIM gateway base URL.')
output gatewayUrl string = apim.properties.gatewayUrl

@description('Object ID of the APIM system-assigned identity.')
output principalId string = apim.identity.principalId

@description('Name of the shared model API.')
output apiName string = api.name

@description('Application Insights name, or empty when observability is disabled.')
output appInsightsName string = enableObservability ? appInsights.name : ''
