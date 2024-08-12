param location string  
param containerRegistryName string  
param containerImage string  
param appName string  
param targetPort int  
  
resource containerApp 'Microsoft.App/containerApps@2022-01-01-preview' = {  
  name: appName  
  location: location  
  properties: {  
    managedEnvironmentId: '/subscriptions/${subscription().subscriptionId}/resourceGroups/${resourceGroup().name}/providers/Microsoft.App/managedEnvironments/default'  
    configuration: {  
      ingress: {  
        external: true  
        targetPort: targetPort  
      }  
    }  
    template: {  
      containers: [  
        {  
          name: appName  
          image: '${containerRegistryName}.azurecr.io/${containerImage}'  
          resources: {  
            cpu: 1  
            memory: '1Gi'  
          }  
          env: [  
            {  
              name: 'FASTAPI_URL'  
              value: 'http://python-service:8000'  
            }  
          ]  
        }  
      ]  
    }  
  }  
}  
