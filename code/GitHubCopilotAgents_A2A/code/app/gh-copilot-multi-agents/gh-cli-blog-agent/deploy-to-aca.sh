#!/bin/bash
# Deploy gh-cli-blog-agent to Azure Container Apps
# This script will:
# 1. Create a resource group
# 2. Create an Azure Container Registry (ACR)
# 3. Build and push the Docker image to ACR
# 4. Create a Container Apps environment
# 5. Deploy the Container App with the COPILOT_GITHUB_TOKEN secret

set -e

# Configuration
RESOURCE_GROUP="Your Azure Resource Group"
LOCATION="Your Azure Region"
ACR_NAME="Your Azure Container Registry Name"
ENVIRONMENT="Your Container Apps Environment Name"
APP_NAME="Your Container App Name"
IMAGE_NAME="Your Docker Image Name"

# Secret - GitHub Copilot Token
COPILOT_GITHUB_TOKEN="Your GitHub Token"

echo "=========================================="
echo "Deploying gh-cli-blog-agent to Azure Container Apps"
echo "=========================================="
echo "Resource Group: $RESOURCE_GROUP"
echo "Location: $LOCATION"
echo "Container App: $APP_NAME"
echo "=========================================="

# Step 1: Create Resource Group
echo ""
echo "Step 1: Creating resource group..."
az group create --name $RESOURCE_GROUP --location $LOCATION -o table

# Step 2: Create Azure Container Registry
echo ""
echo "Step 2: Creating Azure Container Registry..."
az acr create --resource-group $RESOURCE_GROUP \
  --name $ACR_NAME \
  --sku Basic \
  --admin-enabled true \
  -o table

# Get ACR credentials
ACR_LOGIN_SERVER=$(az acr show --name $ACR_NAME --query loginServer -o tsv)
ACR_USERNAME=$(az acr credential show --name $ACR_NAME --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)

echo "ACR Login Server: $ACR_LOGIN_SERVER"

# Step 3: Build and push Docker image to ACR
echo ""
echo "Step 3: Building and pushing Docker image to ACR..."
az acr build --registry $ACR_NAME \
  --image $IMAGE_NAME:latest \
  --file Dockerfile \
  .

# Step 4: Create Container Apps Environment
echo ""
echo "Step 4: Creating Container Apps Environment..."
az containerapp env create \
  --name $ENVIRONMENT \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  -o table

# Step 5: Deploy Container App with secret
echo ""
echo "Step 5: Deploying Container App with COPILOT_GITHUB_TOKEN secret..."
az containerapp create \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --environment $ENVIRONMENT \
  --image "$ACR_LOGIN_SERVER/$IMAGE_NAME:latest" \
  --registry-server $ACR_LOGIN_SERVER \
  --registry-username $ACR_USERNAME \
  --registry-password $ACR_PASSWORD \
  --target-port 8001 \
  --ingress external \
  --cpu 1 \
  --memory 2Gi \
  --min-replicas 1 \
  --max-replicas 3 \
  --secrets "copilot-github-token=$COPILOT_GITHUB_TOKEN" \
  --env-vars "COPILOT_GITHUB_TOKEN=secretref:copilot-github-token" "AGENT_PORT=8001" \
  -o table

# Get the FQDN of the deployed app
echo ""
echo "=========================================="
echo "Deployment completed successfully!"
echo "=========================================="
APP_URL=$(az containerapp show --name $APP_NAME --resource-group $RESOURCE_GROUP --query properties.configuration.ingress.fqdn -o tsv)
echo "Your application is available at: https://$APP_URL"
echo ""
echo "To view logs:"
echo "  az containerapp logs show --name $APP_NAME --resource-group $RESOURCE_GROUP --follow"
echo ""
echo "To delete all resources:"
echo "  az group delete --name $RESOURCE_GROUP --yes --no-wait"
