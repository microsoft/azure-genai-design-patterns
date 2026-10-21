// ILLUSTRATIVE AND UNDEPLOYED - validated with `az bicep build` only.
//
// Shared Microsoft Foundry (AIServices) account plus the one model deployment that every team shares.
// Local (key) auth is disabled: the only caller of the model endpoint is the APIM managed identity, and
// team members reach their project with Microsoft Entra ID.

@description('Azure region.')
param location string

@description('Foundry account name; also used as the custom subdomain.')
param accountName string

@description('Public network access for the account.')
@allowed([
  'Enabled'
  'Disabled'
])
param publicNetworkAccess string = 'Enabled'

@description('Shared model deployment name.')
param modelDeploymentName string

@description('Model name.')
param modelName string

@description('Model version.')
param modelVersion string

@description('Deployment SKU, for example GlobalStandard.')
param modelSkuName string

@description('Deployment capacity in units of the deployment SKU.')
@minValue(1)
param modelCapacity int

@description('Tags.')
param tags object = {}

resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: accountName
  location: location
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  tags: tags
  properties: {
    allowProjectManagement: true
    customSubDomainName: accountName
    disableLocalAuth: true
    publicNetworkAccess: publicNetworkAccess
  }
}

resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: account
  name: modelDeploymentName
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
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

@description('Foundry account name.')
output accountName string = account.name

@description('Foundry account resource ID.')
output accountId string = account.id

@description('Custom subdomain used to build the OpenAI and project endpoints.')
output customSubDomainName string = account.properties.customSubDomainName

@description('Shared model deployment name.')
output modelDeploymentName string = modelDeployment.name
