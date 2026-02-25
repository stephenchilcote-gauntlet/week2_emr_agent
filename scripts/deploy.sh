#!/usr/bin/env bash
# deploy.sh — Deploy or update the EMR agent on a Hetzner VPS.
# Usage: ./scripts/deploy.sh <server-ip>
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <server-ip>"
  exit 1
fi

SERVER="root@$1"
REMOTE_DIR="/opt/emr-agent"

echo "=== Syncing project files to $SERVER:$REMOTE_DIR ==="
rsync -avz --delete \
  --exclude '.venv' \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  --exclude '.hypothesis' \
  --exclude '.mypy_cache' \
  --exclude '.ruff_cache' \
  --exclude 'openemr' \
  --exclude 'data' \
  --exclude 'tests' \
  --exclude '.mutmut-cache' \
  --exclude '*.pyc' \
  --exclude 'htmlcov' \
  --exclude '.coverage' \
  ./ "$SERVER:$REMOTE_DIR/"

echo "=== Copying .env.prod to server ==="
scp .env.prod "$SERVER:$REMOTE_DIR/.env"

echo "=== Building and starting services ==="
ssh "$SERVER" "cd $REMOTE_DIR && docker compose -f docker-compose.prod.yml --env-file .env up -d --build"

echo ""
echo "=== Deploy complete ==="
echo "OpenEMR:  http://$1"
echo "Agent:    Not publicly exposed (use: ssh -L 8000:localhost:8000 root@$1)"
echo "Jaeger:   Not publicly exposed (use: ssh -L 16686:localhost:16686 root@$1)"
