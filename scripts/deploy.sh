#!/usr/bin/env bash
# deploy.sh — Deploy or update the EMR agent on a Hetzner VPS.
#
# Usage:
#   ./scripts/deploy.sh <server-ip>              # normal deploy (preserves DB)
#   ./scripts/deploy.sh <server-ip> --fresh       # wipe volumes and re-register OAuth
#
# After a --fresh deploy, the script automatically:
#   1. Waits for OpenEMR to boot
#   2. Registers an OAuth client with password grant
#   3. Enables it in the database
#   4. Injects credentials into .env and restarts the agent
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <server-ip> [--fresh]"
  exit 1
fi

SERVER="root@$1"
REMOTE_DIR="/opt/emr-agent"
DOMAIN="emragent.404.mn"
FRESH=false
if [ "${2:-}" = "--fresh" ]; then
  FRESH=true
fi

if [ "$FRESH" = true ]; then
  echo "=== FRESH DEPLOY: Wiping all containers, volumes, and images ==="
  ssh -o StrictHostKeyChecking=no "$SERVER" \
    "cd $REMOTE_DIR 2>/dev/null && docker compose -f docker-compose.prod.yml down -v --rmi all 2>/dev/null; docker system prune -af 2>/dev/null; rm -rf $REMOTE_DIR/*; mkdir -p $REMOTE_DIR; echo 'Wiped clean.'" || true

  echo "=== Checking Let's Encrypt certificates ==="
  CERT_EXISTS=$(ssh -o StrictHostKeyChecking=no "$SERVER" \
    "test -f /etc/letsencrypt/live/$DOMAIN/fullchain.pem && echo yes || echo no")
  if [ "$CERT_EXISTS" = "no" ]; then
    echo "No certs found — obtaining via certbot standalone..."
    ssh -o StrictHostKeyChecking=no "$SERVER" \
      "apt-get install -y certbot >/dev/null 2>&1; certbot certonly --standalone --non-interactive --agree-tos --register-unsafely-without-email -d $DOMAIN"
    echo "Certificates obtained."
  else
    echo "Certificates already present at /etc/letsencrypt/live/$DOMAIN/"
  fi
fi

echo "=== Syncing project files to $SERVER:$REMOTE_DIR ==="
rsync -avz --delete \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '.hypothesis' \
  --exclude '.mypy_cache' \
  --exclude '.ruff_cache' \
  --exclude 'data' \
  --exclude 'tests' \
  --exclude '.mutmut-cache' \
  --exclude '*.pyc' \
  --exclude 'htmlcov' \
  --exclude '.coverage' \
  --exclude 'openemr/.git' \
  --exclude 'openemr/tests' \
  --exclude 'openemr/.github' \
  --exclude 'openemr/ci' \
  --exclude 'openemr/Documentation' \
  --exclude 'openemr/.phpstan' \
  ./ "$SERVER:$REMOTE_DIR/"

echo "=== Copying .env.prod to server ==="
scp .env.prod "$SERVER:$REMOTE_DIR/.env"

echo "=== Building and starting services ==="
ssh -o StrictHostKeyChecking=no "$SERVER" "cd $REMOTE_DIR && docker compose -f docker-compose.prod.yml --env-file .env up -d --build"

if [ "$FRESH" = true ]; then
  echo ""
  echo "=== Fresh deploy: Registering OAuth client ==="
  # Source .env.prod to get MYSQL_PASSWORD and OPENEMR_PASS for the registration script
  set -a
  # shellcheck disable=SC1091
  source .env.prod
  set +a
  ./scripts/register-oauth.sh "$1"

  echo ""
  echo "=== Saving OAuth credentials back to local .env.prod ==="
  # Pull the updated .env back so future deploys preserve the credentials
  scp "$SERVER:$REMOTE_DIR/.env" .env.prod
  echo "Updated .env.prod with OAuth credentials."
fi

echo ""
echo "=== Deploy complete ==="
echo "OpenEMR:  https://$1"
echo "Agent:    Not publicly exposed (use: ssh -L 8000:localhost:8000 $SERVER)"
echo "Jaeger:   Not publicly exposed (use: ssh -L 16686:localhost:16686 $SERVER)"
