#!/bin/bash


print_status() {
    echo -e "\e[32m$1\e[0m"
}


print_status "Sourcing config and secrets..."
source ./.env

required_vars=(
    "RESOURCE_GROUP"
    "ENVIRONMENT"
    "LOCATION"
    "FRONTEND_NAME"
    "FRONTEND_IMAGE"
    "FRONTEND_TARGET_PORT"
    "BACKEND_NAME"
    "BACKEND_IMAGE"
    "TOKEN_BACKEND_IMAGE"
    "BACKEND_TARGET_PORT"
    "ACR_NAME" 
    REDIS_SERVER
    REDIS_PORT
    REDIS_PASSWORD
)


# Loop through the required variables and check if they are set
for var in "${required_vars[@]}"; do
    if [ -z "${!var}" ]; then
        read -rp "Please enter the value for $var: " "$var"
    fi
done




print_status "Checking if resource group $RESOURCE_GROUP exists..."
resource_group=$(az group show -n $RESOURCE_GROUP | jq -r '.id')

if [ -z "${resource_group}" ]
then
    print_status "\e[32m\nResource group $RESOURCE_GROUP not found. Creating...\n\e[0m"
    az group create -n $RESOURCE_GROUP -l $LOCATION
fi


#check if containerapp environment exists
print_status "Checking if containerapp environment $ENVIRONMENT exists..."
containerapp_environment=$(az containerapp env show -n $ENVIRONMENT -g $RESOURCE_GROUP | jq -r '.id')

if [ -z "${containerapp_environment}" ]
then
    print_status "\e[32m\nContainer App Environment $ENVIRONMENT not found. Creating...\n\e[0m"
    az containerapp env create -n $ENVIRONMENT --location $LOCATION -g $RESOURCE_GROUP   
fi

if [ $? -ne 0 ]
then
    printf "\nError configuring containerapp environment. Exiting...\n"
    exit 1
fi



# Define the environment variables as an array
config=(
"AZ_OPENAI_API_KEY=$AZ_OPENAI_API_KEY"
"AZ_OPENAI_ENDPOINT=$AZ_OPENAI_ENDPOINT"
"AZURE_OPENAI_ENDPOINT_2=$AZURE_OPENAI_ENDPOINT_2"
"AZURE_OPENAI_API_KEY_2=$AZURE_OPENAI_API_KEY_2"
"AZURE_OPENAI_CHAT_DEPLOYMENT_2=$AZURE_OPENAI_CHAT_DEPLOYMENT_2"
"AZURE_OPENAI_ENDPOINT_1=$AZURE_OPENAI_ENDPOINT_1"
"AZURE_OPENAI_API_KEY_1=$AZURE_OPENAI_API_KEY_1"
"AZURE_OPENAI_CHAT_DEPLOYMENT_1=$AZURE_OPENAI_CHAT_DEPLOYMENT_1"
"AZURE_OPENAI_EMB_DEPLOYMENT_2=$AZURE_OPENAI_EMB_DEPLOYMENT_2"
"MODEL=$MODEL"
"AZURE_OPENAI_CHATGPT_DEPLOYMENT=$AZURE_OPENAI_CHATGPT_DEPLOYMENT"
"SPEECH_API_KEY=$SPEECH_API_KEY"
"SPEECH_REGION=$SPEECH_REGION"
"AZURE_SEARCH_SERVICE_ENDPOINT=$AZURE_SEARCH_SERVICE_ENDPOINT"
"AZURE_SEARCH_INDEX_NAME=$AZURE_SEARCH_INDEX_NAME"
"AZURE_SEARCH_ADMIN_KEY=$AZURE_SEARCH_ADMIN_KEY"
"TOKEN_BACKEND_TARGET_PORT=$TOKEN_BACKEND_TARGET_PORT"
"REDIS_SERVER=$REDIS_SERVER"
"REDIS_PORT=$REDIS_PORT"
"REDIS_PASSWORD=$REDIS_PASSWORD"

)

# # Build and push BACKEND image using ACR Tasks  
# print_status "Building $BACKEND_NAME image in ACR..."  
# cd backend

# az acr build --registry $ACR_NAME --image $BACKEND_IMAGE ./  

# containerAppDefaultDomain="https://$BACKEND_NAME.$(az containerapp env show -n $ENVIRONMENT -g $RESOURCE_GROUP | jq -r '.properties.defaultDomain')"

# print_status "Deploying BACKEND Container App..."

# print_status "Creating Container App for BACKEND..."  
# az containerapp create   \
# --name $BACKEND_NAME   \
# --resource-group $RESOURCE_GROUP   \
# --environment $ENVIRONMENT   \
# --image $ACR_NAME.azurecr.io/$BACKEND_IMAGE    \
# --min-replicas 1 \
# --max-replicas 1 \
# --target-port $BACKEND_TARGET_PORT   \
# --ingress 'external'   \
# --registry-server $ACR_NAME.azurecr.io   \
# --query properties.configuration.ingress \
# --env-vars "${config[@]}"


# echo "Done! $BACKEND_NAME is deployed to $containerAppDefaultDomain"

# cd ..

# cd tokenbackend

# containerAppDefaultDomain_TOKEN="https://$TOKEN_BACKEND_NAME.$(az containerapp env show -n $ENVIRONMENT -g $RESOURCE_GROUP | jq -r '.properties.defaultDomain')"

# print_status "Deploying TOKEN BACKEND Container App..."

# # Build and push TOKEN BACKEND image using ACR Tasks  
# print_status "Building $TOKEN_BACKEND_NAME image in ACR..."  

# az acr build --registry $ACR_NAME --image $TOKEN_BACKEND_IMAGE ./  

# print_status "Creating Container App for TOKEN BACKEND..."  
# az containerapp create   \
# --name $TOKEN_BACKEND_NAME   \
# --resource-group $RESOURCE_GROUP   \
# --environment $ENVIRONMENT   \
# --image $ACR_NAME.azurecr.io/$TOKEN_BACKEND_IMAGE    \
# --min-replicas 1 \
# --max-replicas 1 \
# --target-port $TOKEN_BACKEND_TARGET_PORT   \
# --ingress 'external'   \
# --registry-server $ACR_NAME.azurecr.io   \
# --query properties.configuration.ingress \
# --env-vars "${config[@]}"


# echo "Done! $TOKEN_BACKEND_NAME is deployed to $containerAppDefaultDomain_TOKEN"

# cd ..

containerAppDefaultDomain_FrontEnd="https://$FRONTEND_NAME.$(az containerapp env show -n $ENVIRONMENT -g $RESOURCE_GROUP | jq -r '.properties.defaultDomain')"


print_status "Deploying FRONTEND Container App..."

#build frontend image
print_status "Building $FRONTEND_NAME image..."
cd frontend
# cat << EOF > src/config.json
# {
#     "backendAPIHost" : "$containerAppDefaultDomain",
#     "tokenAPIHost" : "$containerAppDefaultDomain_TOKEN"    
  
# }
# EOF

# Build and push FRONTEND image using ACR Tasks  
print_status "Building $FRONTEND_NAME image in ACR..."  
# Assuming the frontend Dockerfile does not require npm install and npm run build to be run outside of the Docker build process.   
# If it does, you may need to adjust the Dockerfile to incorporate these steps.  
az acr build --registry $ACR_NAME --image $FRONTEND_IMAGE ./  
# Creating Container App for FRONTEND  

print_status "Creating Container App for FRONTEND..."  
az containerapp create   \
--name $FRONTEND_NAME   \
--resource-group $RESOURCE_GROUP   \
--environment $ENVIRONMENT   \
--image $ACR_NAME.azurecr.io/$FRONTEND_IMAGE    \
--min-replicas 1 \
--max-replicas 1 \
--target-port $FRONTEND_TARGET_PORT   \
--ingress 'external'   \
--registry-server $ACR_NAME.azurecr.io   \
--query properties.configuration.ingress  


echo "Done! $FRONTEND_NAME is deployed to $containerAppDefaultDomain_FrontEnd"