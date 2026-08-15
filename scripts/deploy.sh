#!/usr/bin/env bash
#
# Deploy Aurea to Azure Container Apps — backend, worker and frontend together.
#
#   ./scripts/deploy.sh              # deploy everything (default)
#   ./scripts/deploy.sh backend      # backend + worker only
#   ./scripts/deploy.sh frontend     # frontend only
#   ./scripts/deploy.sh verify       # no deploy, just check what is live
#
# From a fresh Cloud Shell (clones if absent, so it is safe to paste every time):
#
#   [ -d ~/aurea ] || git clone https://github.com/swarupd227/Aurea.git ~/aurea
#   cd ~/aurea && git pull && bash scripts/deploy.sh
#
# If the subscription is not the default one, set it first:
#   az account set --subscription "<name-or-id>"
#
# Why this script exists:
#   1. It deploys the backend AND the frontend. Deploying only ./frontend left the
#      API stale for days — the nav entry for Regulatory Countdown shipped with the
#      frontend while the endpoint behind it did not exist, so the page 404'd.
#   2. It tags every image with a unique commit+timestamp tag. Updating a container
#      app to an image reference it is already running can be a no-op, because the
#      spec has not changed and no new revision is created. A unique tag makes the
#      spec change every time, so a pull is always forced.
#   3. It verifies afterwards. A deploy that silently did nothing looks exactly like
#      a deploy that worked, which is what made this hard to see.

set -euo pipefail

RG="${AUREA_RG:-aurea-rg}"
ACR="${AUREA_ACR:-aureaartizentreg}"
BACKEND_APP="${AUREA_BACKEND_APP:-aurea-backend}"
WORKER_APP="${AUREA_WORKER_APP:-aurea-worker}"
FRONTEND_APP="${AUREA_FRONTEND_APP:-aurea-frontend}"

TARGET="${1:-all}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
info() { printf '  · %s\n' "$*"; }

# Unique, traceable, and sortable: 20260814-071530-c28fe28
TAG="$(date -u +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD)"

fqdn() {
  az containerapp show --name "$1" --resource-group "$RG" \
    --query properties.configuration.ingress.fqdn -o tsv 2>/dev/null || true
}

# ---------------------------------------------------------------- build/deploy

deploy_backend() {
  bold "Backend → $ACR/aurea-backend:$TAG"
  az acr build --registry "$ACR" \
    --image "aurea-backend:$TAG" \
    --image "aurea-backend:latest" \
    ./backend
  ok "image built"

  az containerapp update --name "$BACKEND_APP" --resource-group "$RG" \
    --image "$ACR.azurecr.io/aurea-backend:$TAG" --output none
  ok "$BACKEND_APP updated"

  # The worker runs the same image; leaving it behind causes agent runs to execute
  # against different code than the API serves.
  if az containerapp show --name "$WORKER_APP" --resource-group "$RG" &>/dev/null; then
    az containerapp update --name "$WORKER_APP" --resource-group "$RG" \
      --image "$ACR.azurecr.io/aurea-backend:$TAG" --output none
    ok "$WORKER_APP updated"
  else
    info "$WORKER_APP not found — skipped"
  fi
}

deploy_frontend() {
  local api_url
  api_url="https://$(fqdn "$BACKEND_APP")"
  if [[ "$api_url" == "https://" ]]; then
    bad "could not resolve $BACKEND_APP ingress FQDN"
    exit 1
  fi

  bold "Frontend → $ACR/aurea-frontend:$TAG"
  info "API base baked in: $api_url"
  # Passed explicitly rather than relying on the Dockerfile default, so the built
  # bundle can never point at a stale or localhost API.
  az acr build --registry "$ACR" \
    --image "aurea-frontend:$TAG" \
    --image "aurea-frontend:latest" \
    --build-arg "NEXT_PUBLIC_API_BASE_URL=$api_url" \
    ./frontend
  ok "image built"

  az containerapp update --name "$FRONTEND_APP" --resource-group "$RG" \
    --image "$ACR.azurecr.io/aurea-frontend:$TAG" --output none
  ok "$FRONTEND_APP updated"
}

# ---------------------------------------------------------------------- verify

verify() {
  bold "Verifying"
  local be fe fail=0
  be="https://$(fqdn "$BACKEND_APP")"
  fe="https://$(fqdn "$FRONTEND_APP")"

  # Container Apps can take a few seconds to route to the new revision.
  sleep 10

  if curl -fsS --max-time 30 "$be/health" | grep -q '"database":true'; then
    ok "backend healthy, database reachable"
  else
    bad "backend health check failed"; fail=1
  fi

  # Route-level check. A backend that is merely *up* tells you nothing about
  # whether it is running current code — this is the check that would have caught
  # the stale API immediately.
  local routes
  routes="$(curl -fsS --max-time 30 "$be/openapi.json" || echo '')"
  for route in regulatory-countdown run-cip push-to-custodian; do
    if grep -q "$route" <<<"$routes"; then
      ok "route present: $route"
    else
      bad "route MISSING: $route  → backend is running old code"; fail=1
    fi
  done

  if curl -fsS --max-time 30 -o /dev/null -w '%{http_code}' "$fe/login" | grep -q 200; then
    ok "frontend serving"
  else
    bad "frontend not responding"; fail=1
  fi

  echo
  if [[ $fail -eq 0 ]]; then
    bold "All checks passed — tag $TAG"
  else
    bold "Some checks FAILED — see above"
    exit 1
  fi
}

# ------------------------------------------------------------------------ main

case "$TARGET" in
  all)      deploy_backend; deploy_frontend; verify ;;
  backend)  deploy_backend; verify ;;
  frontend) deploy_frontend; verify ;;
  verify)   verify ;;
  *) echo "usage: $0 [all|backend|frontend|verify]" >&2; exit 2 ;;
esac
