param acrName string  
  
resource acr 'Microsoft.ContainerRegistry/registries@2021-06-01-preview' existing = {  
  name: acrName  
}  
  
resource acrRoleAssignment 'Microsoft.Authorization/roleAssignments@2020-04-01-preview' = {  
  name: guid(resourceGroup().id, acr.id, 'acrPull')  
  scope: acr  
  properties: {  
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')  
    principalId: subscription().subscriptionId  
    principalType: 'ServicePrincipal'  
  }  
}  
