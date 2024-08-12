param environmentName string  
param location string = resourceGroup().location  
  
resource containerAppEnv 'Microsoft.App/managedEnvironments@2022-03-01' = {  
  name: environmentName  
  location: location  
}  
  
resource acr 'Microsoft.ContainerRegistry/registries@2021-06-01-preview' = {  
  name: 'myacr${uniqueString(resourceGroup().id)}'  
  location: location  
  sku: {  
    name: 'Basic'  
  }  
  properties: {  
    adminUserEnabled: true  
  }  
}  
  
module acrRoleAssignment './acrRoleAssignment.bicep' = {  
  name: 'acrRoleAssignment'  
  scope: resourceGroup()  
  params: {  
    acrName: acr.name  
  }  
}  
  
resource pythonService 'Microsoft.App/containerApps@2022-03-01' = {  
  name: 'python-service'  
  location: location  
  properties: {  
    managedEnvironmentId: containerAppEnv.id  
    configuration: {  
      ingress: {  
        external: true  
        targetPort: 8000  
      }  
    }  
    template: {  
      containers: [  
        {  
          name: 'python-service'  
          image: '${acr.properties.loginServer}/python-service:latest'  
          env: [  
            {  
              name: 'PORT'  
              value: '8000'  
            }  
          ]  
        }  
      ]  
    }  
  }  
}  
  
resource streamlitApp 'Microsoft.App/containerApps@2022-03-01' = {  
  name: 'streamlit-app'  
  location: location  
  properties: {  
    managedEnvironmentId: containerAppEnv.id  
    configuration: {  
      ingress: {  
        external: true  
        targetPort: 8501  
      }  
    }  
    template: {  
      containers: [  
        {  
          name: 'streamlit-app'  
          image: '${acr.properties.loginServer}/streamlit-app:latest'  
          env: [  
            {  
              name: 'PYTHON_SERVICE_URL'  
              value: 'http://python-service:8000'  
            }  
          ]  
        }  
      ]  
    }  
  }  
}  
