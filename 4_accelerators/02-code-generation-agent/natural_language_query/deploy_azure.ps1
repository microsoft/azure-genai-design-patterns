# Variables  
$RESOURCE_GROUP = "rg-analytic"  
$LOCATION = "westus" # Change to your preferred location  
$CONTAINER_REGISTRY = "analyticassistant"  
$CONTAINER_ENVIRONMENT = "analytic-assistant-python-env"  
$PYTHON_IMAGE = "python_service:latest"  
$STREAMLIT_IMAGE = "streamlit_app:latest"  
  
# Create a resource group  
az group create --name $RESOURCE_GROUP --location $LOCATION  
  
# Create a container registry  
az acr create --resource-group $RESOURCE_GROUP --name $CONTAINER_REGISTRY --sku Basic  
  
# Enable admin rights for the container registry  
az acr update -n $CONTAINER_REGISTRY --admin-enabled true  
  
# Build the Python service image  
az acr build --registry $CONTAINER_REGISTRY --image $PYTHON_IMAGE --file ./Dockerfile.python_service ./  
  
# Build the Streamlit app image  
az acr build --registry $CONTAINER_REGISTRY --image $STREAMLIT_IMAGE --file ./Dockerfile.streamlit_app ./  
  
# Create a container environment  
az containerapp env create --name $CONTAINER_ENVIRONMENT --resource-group $RESOURCE_GROUP --location $LOCATION  
  
# Splatting parameters for the analytic-assistant-python service  
$pythonServiceParams = @{  
    name            = "analytic-assistant-python"  
    "resource-group" = $RESOURCE_GROUP  
    environment     = $CONTAINER_ENVIRONMENT  
    image           = "$CONTAINER_REGISTRY.azurecr.io/$PYTHON_IMAGE"  
    "min-replicas"  = 1  
    "max-replicas"  = 1  
    "target-port"   = 8000  
    ingress         = "external"  
    "registry-server" = "$CONTAINER_REGISTRY.azurecr.io"  
    query           = "properties.configuration.ingress.fqdn"  
    output          = "tsv"  
}  
  
# Deploy the analytic-assistant-python service and get its URL  
$python_service_output = az containerapp create @pythonServiceParams  
  
# Check if the deployment was successful and the URL was retrieved  
if (-not $python_service_output) {  
    Write-Output "Failed to retrieve the URL of the analytic-assistant-python service."  
    exit 1  
}  
  
# Construct the PYTHON_SERVICE_URL  
$PYTHON_SERVICE_URL = "http://$python_service_output"  
  
# Splatting parameters for the analytic-assistant-fe service  
$feServiceParams = @{  
    name            = "analytic-assistant-fe"  
    "resource-group" = $RESOURCE_GROUP  
    environment     = $CONTAINER_ENVIRONMENT  
    image           = "$CONTAINER_REGISTRY.azurecr.io/$STREAMLIT_IMAGE"  
    "min-replicas"  = 1  
    "max-replicas"  = 1  
    "target-port"   = 8501  
    ingress         = "external"  
    "registry-server" = "$CONTAINER_REGISTRY.azurecr.io"  
    "env-vars"      = "PYTHON_SERVICE_URL=$PYTHON_SERVICE_URL"  
    query           = "properties.configuration.ingress.fqdn"  
    output          = "tsv"  
}  
  
# Deploy the analytic-assistant-fe service with the PYTHON_SERVICE_URL environment variable  
$fe_service_output = az containerapp create @feServiceParams  
  
# Check if the deployment was successful  
if (-not $fe_service_output) {  
    Write-Output "Failed to deploy analytic-assistant-fe."  
    exit 1  
} else {  
    Write-Output "Successfully deployed analytic-assistant-fe with URL: http://$fe_service_output"  
}  
