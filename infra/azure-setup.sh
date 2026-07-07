#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Aurea — One-time Azure infrastructure provisioning
#
# Run this ONCE to create all Azure resources. After it finishes, it prints
# the GitHub secrets / variables you need to add — then every push to `main`
# deploys automatically via GitHub Actions.
#
# Prerequisites
#   • Azure CLI installed (https://learn.microsoft.com/cli/azure/install-azure-cli)
#     OR just open https://shell.azure.com (Azure Cloud Shell — no install needed)
#   • Logged in:  az login
#
# Recommended: run inside Azure Cloud Shell (bash) so you don't need to install
# anything locally. Upload this file or paste it into the shell.
#
# Usage
#   chmod +x infra/azure-setup.sh
#   ./infra/azure-setup.sh
#
# Optional overrides (set before running)
#   AZURE_LOCATION=australiaeast   # Azure region closest to you
#   UNIQUE_SUFFIX=myaurea          # 7-char alphanumeric suffix for globally-unique names
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
LOCATION="${AZURE_LOCATION:-eastus}"          # Change to: australiaeast, westeurope, etc.
# Globally-unique suffix — change if names conflict (alphanumeric, max 7 chars)
UNIQUE="${UNIQUE_SUFFIX:-$(LC_ALL=C tr -dc 'a-z0-9' < /dev/urandom 2>/dev/null | head -c 6 || echo "aurea1")}"

RESOURCE_GROUP="aurea-rg"
ACR_NAME="${UNIQUE}aureareg"                   # must be globally unique, 5-50 alphanumeric
PG_SERVER="${UNIQUE}-aurea-pg"                 # must be globally unique
PG_ADMIN="aureapgadmin"
PG_DB="aurea"
REDIS_NAME="${UNIQUE}-aurea-redis"             # must be globally unique
ACA_ENV="aurea-env"
BACKEND_APP="aurea-backend"
WORKER_APP="aurea-worker"
FRONTEND_APP="aurea-frontend"
IDENTITY_NAME="aurea-aca-identity"
SP_NAME="aurea-github-actions"

# ── Generate secrets ──────────────────────────────────────────────────────────
PG_PASSWORD="$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom 2>/dev/null | head -c 20)Ab1!"
JWT_SECRET="$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom 2>/dev/null | head -c 48)"

SUBSCRIPTION_ID=$(az account show --query id -o tsv)

echo ""
echo "══════════════════════════════════════════════════════════════════"
echo "  Aurea — Azure Infrastructure Setup"
echo "══════════════════════════════════════════════════════════════════"
echo "  Subscription  : $SUBSCRIPTION_ID"
echo "  Resource Group: $RESOURCE_GROUP"
echo "  Location      : $LOCATION"
echo "  Unique suffix : $UNIQUE"
echo "══════════════════════════════════════════════════════════════════"
echo ""

# ── 1. Resource group ─────────────────────────────────────────────────────────
echo "▶ [1/9] Resource group..."
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" -o none
echo "  ✓ $RESOURCE_GROUP"

# ── 2. Container Registry ─────────────────────────────────────────────────────
echo "▶ [2/9] Azure Container Registry ($ACR_NAME)..."
az acr create \
  --name "$ACR_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --sku Basic \
  --admin-enabled false \
  -o none
ACR_LOGIN_SERVER="${ACR_NAME}.azurecr.io"
ACR_ID=$(az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)
echo "  ✓ $ACR_LOGIN_SERVER"

# ── 3. PostgreSQL Flexible Server ─────────────────────────────────────────────
echo "▶ [3/9] PostgreSQL Flexible Server ($PG_SERVER) — ~3 min..."
az postgres flexible-server create \
  --name "$PG_SERVER" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --admin-user "$PG_ADMIN" \
  --admin-password "$PG_PASSWORD" \
  --sku-name "Standard_B1ms" \
  --tier "Burstable" \
  --version "16" \
  --storage-size "32" \
  --public-access "0.0.0.0" \
  -o none

# Enable pgvector extension
echo "     Enabling pgvector extension..."
az postgres flexible-server parameter set \
  --server-name "$PG_SERVER" \
  --resource-group "$RESOURCE_GROUP" \
  --name "azure.extensions" \
  --value "vector" \
  -o none

# Create database
az postgres flexible-server db create \
  --server-name "$PG_SERVER" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$PG_DB" \
  -o none

PG_HOST="${PG_SERVER}.postgres.database.azure.com"
# Azure PostgreSQL requires SSL; asyncpg reads ssl=require from the URL
PG_URL="postgresql+asyncpg://${PG_ADMIN}:${PG_PASSWORD}@${PG_HOST}/${PG_DB}?ssl=require"
echo "  ✓ $PG_HOST"

# ── 4. Redis Cache ────────────────────────────────────────────────────────────
echo "▶ [4/9] Azure Cache for Redis ($REDIS_NAME) — ~5 min..."
az redis create \
  --name "$REDIS_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  --sku "Basic" \
  --vm-size "c0" \
  -o none

REDIS_HOST="${REDIS_NAME}.redis.cache.windows.net"
REDIS_PASSWORD=$(az redis list-keys \
  --name "$REDIS_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  --query primaryKey -o tsv)
# Azure Redis uses port 6380 (SSL) and the rediss:// scheme
REDIS_URL="rediss://:${REDIS_PASSWORD}@${REDIS_HOST}:6380/0"
echo "  ✓ $REDIS_HOST:6380 (SSL)"

# ── 5. Container Apps Environment ─────────────────────────────────────────────
echo "▶ [5/9] Container Apps Environment ($ACA_ENV)..."
az containerapp env create \
  --name "$ACA_ENV" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  -o none
echo "  ✓ $ACA_ENV"

# ── 6. Managed identity + ACR pull permission ─────────────────────────────────
echo "▶ [6/9] Managed identity + ACR permissions..."
az identity create \
  --name "$IDENTITY_NAME" \
  --resource-group "$RESOURCE_GROUP" \
  -o none

IDENTITY_ID=$(az identity show --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)
IDENTITY_CLIENT_ID=$(az identity show --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP" --query clientId -o tsv)
IDENTITY_PRINCIPAL_ID=$(az identity show --name "$IDENTITY_NAME" --resource-group "$RESOURCE_GROUP" --query principalId -o tsv)

# Allow the identity to pull images from ACR
az role assignment create \
  --assignee-object-id "$IDENTITY_PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "AcrPull" \
  --scope "$ACR_ID" \
  -o none
echo "  ✓ AcrPull granted to managed identity"

# ── 7a. Backend Container App ─────────────────────────────────────────────────
echo "▶ [7/9] Container Apps (backend, worker, frontend)..."
echo "     Creating backend..."

# Bootstrap with a public placeholder image; GitHub Actions replaces it on first push.
az containerapp create \
  --name "$BACKEND_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --environment "$ACA_ENV" \
  --image "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest" \
  --user-assigned "$IDENTITY_ID" \
  --registry-server "$ACR_LOGIN_SERVER" \
  --registry-identity "$IDENTITY_ID" \
  --target-port 8000 \
  --ingress "external" \
  --min-replicas 1 \
  --max-replicas 3 \
  --cpu "0.5" \
  --memory "1.0Gi" \
  --secrets \
    "db-url=${PG_URL}" \
    "redis-url=${REDIS_URL}" \
    "jwt-secret=${JWT_SECRET}" \
    "anthropic-key=" \
  --env-vars \
    "DATABASE_URL=secretref:db-url" \
    "REDIS_URL=secretref:redis-url" \
    "AUREA_JWT_SECRET=secretref:jwt-secret" \
    "ANTHROPIC_API_KEY=secretref:anthropic-key" \
    "AUREA_ENV=production" \
    "AUREA_RUN_MIGRATIONS=true" \
    "AUREA_RUN_SEED=true" \
    "AUREA_LOG_LEVEL=INFO" \
  -o none

BACKEND_FQDN=$(az containerapp show \
  --name "$BACKEND_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv)
BACKEND_URL="https://${BACKEND_FQDN}"

# ── 7b. Worker Container App ──────────────────────────────────────────────────
echo "     Creating worker..."
az containerapp create \
  --name "$WORKER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --environment "$ACA_ENV" \
  --image "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest" \
  --user-assigned "$IDENTITY_ID" \
  --registry-server "$ACR_LOGIN_SERVER" \
  --registry-identity "$IDENTITY_ID" \
  --ingress "disabled" \
  --min-replicas 1 \
  --max-replicas 1 \
  --cpu "0.5" \
  --memory "1.0Gi" \
  --command "python" \
  --args "-m" "app.atlas.worker" \
  --secrets \
    "db-url=${PG_URL}" \
    "redis-url=${REDIS_URL}" \
    "jwt-secret=${JWT_SECRET}" \
    "anthropic-key=" \
  --env-vars \
    "DATABASE_URL=secretref:db-url" \
    "REDIS_URL=secretref:redis-url" \
    "AUREA_JWT_SECRET=secretref:jwt-secret" \
    "ANTHROPIC_API_KEY=secretref:anthropic-key" \
    "AUREA_ENV=production" \
    "AUREA_ROLE=worker" \
    "AUREA_RUN_MIGRATIONS=false" \
    "AUREA_RUN_SEED=false" \
  -o none

# ── 7c. Frontend Container App ────────────────────────────────────────────────
echo "     Creating frontend..."
az containerapp create \
  --name "$FRONTEND_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --environment "$ACA_ENV" \
  --image "mcr.microsoft.com/azuredocs/containerapps-helloworld:latest" \
  --user-assigned "$IDENTITY_ID" \
  --registry-server "$ACR_LOGIN_SERVER" \
  --registry-identity "$IDENTITY_ID" \
  --target-port 3000 \
  --ingress "external" \
  --min-replicas 1 \
  --max-replicas 3 \
  --cpu "0.25" \
  --memory "0.5Gi" \
  --env-vars \
    "NODE_ENV=production" \
  -o none

FRONTEND_FQDN=$(az containerapp show \
  --name "$FRONTEND_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv)
FRONTEND_URL="https://${FRONTEND_FQDN}"

echo "  ✓ Backend  : $BACKEND_URL"
echo "  ✓ Frontend : $FRONTEND_URL"

# ── 8. Service Principal for GitHub Actions ───────────────────────────────────
echo "▶ [8/9] GitHub Actions service principal ($SP_NAME)..."

# Contributor on the resource group (deploy) + AcrPush (push images)
SP_JSON=$(az ad sp create-for-rbac \
  --name "$SP_NAME" \
  --role "Contributor" \
  --scopes "/subscriptions/${SUBSCRIPTION_ID}/resourceGroups/${RESOURCE_GROUP}" \
  --sdk-auth 2>/dev/null)

SP_CLIENT_ID=$(echo "$SP_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['clientId'])" 2>/dev/null || \
               echo "$SP_JSON" | grep -o '"clientId": *"[^"]*"' | cut -d'"' -f4)

az role assignment create \
  --assignee "$SP_CLIENT_ID" \
  --role "AcrPush" \
  --scope "$ACR_ID" \
  -o none

echo "  ✓ Service principal created with Contributor + AcrPush"

# ── 9. Output instructions ────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════════════════════"
echo "  ✅  INFRASTRUCTURE READY"
echo "══════════════════════════════════════════════════════════════════"
echo ""
echo "  Frontend  : $FRONTEND_URL   (shows placeholder until first deploy)"
echo "  Backend   : $BACKEND_URL"
echo ""
echo "══════════════════════════════════════════════════════════════════"
echo "  STEP 1 — Add GitHub SECRETS"
echo "  (repo → Settings → Secrets and variables → Actions → New secret)"
echo "══════════════════════════════════════════════════════════════════"
echo ""
echo "  Secret name: AZURE_CREDENTIALS"
echo "  Value (paste the entire JSON block):"
echo ""
echo "$SP_JSON"
echo ""
echo "  Secret name: ANTHROPIC_API_KEY"
echo "  Value: <your key from console.anthropic.com>"
echo ""
echo "══════════════════════════════════════════════════════════════════"
echo "  STEP 2 — Add GitHub VARIABLES"
echo "  (repo → Settings → Secrets and variables → Actions → Variables tab → New)"
echo "══════════════════════════════════════════════════════════════════"
echo ""
echo "  AZURE_RG      $RESOURCE_GROUP"
echo "  ACR_NAME      $ACR_NAME"
echo ""
echo "══════════════════════════════════════════════════════════════════"
echo "  STEP 3 — Push to main"
echo "  GitHub Actions will build + deploy Aurea automatically."
echo "══════════════════════════════════════════════════════════════════"
echo ""

# Save a summary file for reference
cat > infra/azure-resources.txt <<SUMMARY
Aurea — Azure Resources
=======================
Subscription  : $SUBSCRIPTION_ID
Resource Group: $RESOURCE_GROUP
Location      : $LOCATION

Container Registry : $ACR_LOGIN_SERVER
PostgreSQL Server  : $PG_HOST
Redis Host         : $REDIS_HOST:6380

Container Apps:
  Backend   : $BACKEND_URL
  Frontend  : $FRONTEND_URL

GitHub Variables (not secrets):
  AZURE_RG   = $RESOURCE_GROUP
  ACR_NAME   = $ACR_NAME
SUMMARY

echo "  Summary saved to infra/azure-resources.txt"
echo ""
