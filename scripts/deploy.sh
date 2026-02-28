#!/usr/bin/env bash
# deploy.sh — Deploy or update the EMR agent on a Hetzner VPS.
#
# Usage:
#   ./scripts/deploy.sh <server-ip>              # agent-only deploy (~1 min)
#   ./scripts/deploy.sh <server-ip> --all         # rebuild everything incl. OpenEMR
#   ./scripts/deploy.sh <server-ip> --fresh       # wipe volumes and re-register OAuth
#
# The default (no flag) deploys only the agent service — it skips syncing the
# 2.1GB openemr/ directory and doesn't restart OpenEMR.  Use --all when you've
# changed sidebar files, Dockerfile.openemr, or start.sh.
#
# After a --fresh deploy, the script automatically:
#   1. Waits for OpenEMR to boot
#   2. Registers an OAuth client with password grant
#   3. Enables it in the database
#   4. Injects credentials into .env and restarts the agent
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <server-ip> [--all|--fresh]"
  exit 1
fi

# Strip protocol prefix and trailing slashes so users can pass a URL or bare host
HOST="${1#https://}"
HOST="${HOST#http://}"
HOST="${HOST%/}"
SERVER="root@$HOST"
REMOTE_DIR="/opt/emr-agent"
DOMAIN="emragent.404.mn"
FRESH=false
DEPLOY_ALL=false
if [ "${2:-}" = "--fresh" ]; then
  FRESH=true
  DEPLOY_ALL=true
elif [ "${2:-}" = "--all" ]; then
  DEPLOY_ALL=true
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

# ---------------------------------------------------------------------------
# Pre-deploy: reject hardcoded Docker bridge IPs / non-localhost URLs in
# deployed source files.  Catches accidental 172.x.x.x or 192.168.x.x IPs
# that coding agents sometimes introduce.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Pre-deploy: preflight-check validates .env.prod before we touch the server.
# Catches Hall of Fame bugs #1 (wrong password) and #4 (.env overwrite).
# ---------------------------------------------------------------------------
echo "=== Pre-deploy: preflight check ==="
PREFLIGHT_FLAG=""
[ "$FRESH" = true ] && PREFLIGHT_FLAG="--fresh"
if ! "$(dirname "$0")/preflight-check.sh" "$HOST" $PREFLIGHT_FLAG; then
  echo ""
  echo "Preflight check FAILED. Fix the issues above before deploying."
  echo "If you're sure, re-run with SKIP_PREFLIGHT=1 to bypass."
  if [ "${SKIP_PREFLIGHT:-0}" != "1" ]; then
    exit 1
  fi
  echo "SKIP_PREFLIGHT=1 set — continuing anyway."
fi

echo "=== Pre-deploy: checking for hardcoded URLs ==="
BAD_PATTERNS='(https?://172\.[0-9]+\.[0-9]+\.[0-9]+|https?://192\.168\.[0-9]+\.[0-9]+|https?://10\.[0-9]+\.[0-9]+\.[0-9]+)'
# Only scan files that end up inside containers (src/, web/, openemr module PHP)
BAD_FILES=$(grep -rEn "$BAD_PATTERNS" \
  --include='*.py' --include='*.php' --include='*.js' --include='*.css' --include='*.html' \
  src/ web/ openemr/interface/modules/custom_modules/ 2>/dev/null || true)

if [ -n "$BAD_FILES" ]; then
  echo ""
  echo "ERROR: Found hardcoded private-network URLs in deployed source files:"
  echo "$BAD_FILES"
  echo ""
  echo "These break in Docker where services communicate via DNS names (e.g. http://agent:8000)."
  echo "Fix the offending file(s) and re-run."
  exit 1
fi
echo "No hardcoded private-network URLs found. OK."

RSYNC_EXCLUDES=(
  --exclude '.venv'
  --exclude '__pycache__'
  --exclude '.pytest_cache'
  --exclude '.hypothesis'
  --exclude '.mypy_cache'
  --exclude '.ruff_cache'
  --exclude 'data'
  --exclude 'tests'
  --exclude '.mutmut-cache'
  --exclude '*.pyc'
  --exclude 'htmlcov'
  --exclude '.coverage'
  --exclude '.git'
)

if [ "$DEPLOY_ALL" = true ]; then
  # Full sync includes openemr/ (2.1 GB) — only needed for sidebar/OpenEMR changes
  RSYNC_EXCLUDES+=(
    --exclude 'openemr/.git'
    --exclude 'openemr/tests'
    --exclude 'openemr/.github'
    --exclude 'openemr/ci'
    --exclude 'openemr/Documentation'
    --exclude 'openemr/.phpstan'
  )
  SERVICES=""  # all services
  echo "=== Full sync (including openemr/) to $SERVER:$REMOTE_DIR ==="
else
  # Agent-only: skip the 2.1 GB openemr/ directory entirely
  RSYNC_EXCLUDES+=(--exclude 'openemr/')
  SERVICES="agent"
  echo "=== Agent-only sync (skipping openemr/) to $SERVER:$REMOTE_DIR ==="
fi

rsync -avz --delete \
  "${RSYNC_EXCLUDES[@]}" \
  ./ "$SERVER:$REMOTE_DIR/"

echo "=== Copying .env.prod to server ==="
scp .env.prod "$SERVER:$REMOTE_DIR/.env"

if [ -n "$SERVICES" ]; then
  echo "=== Building and restarting: $SERVICES ==="
  ssh -o StrictHostKeyChecking=no "$SERVER" "cd $REMOTE_DIR && docker compose -f docker-compose.prod.yml --env-file .env up -d --build $SERVICES"
else
  echo "=== Building and starting all services ==="
  ssh -o StrictHostKeyChecking=no "$SERVER" "cd $REMOTE_DIR && docker compose -f docker-compose.prod.yml --env-file .env up -d --build"

  echo "=== Waiting for OpenEMR to be healthy ==="
  MAX_ATTEMPTS=60
  ATTEMPT=0
  while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    if ssh -o StrictHostKeyChecking=no "$SERVER" "timeout 2 bash -c 'cat < /dev/null > /dev/tcp/localhost/80' 2>/dev/null"; then
      echo "OpenEMR is up on port 80."
      break
    fi
    ATTEMPT=$((ATTEMPT + 1))
    if [ $ATTEMPT -lt $MAX_ATTEMPTS ]; then
      echo "Waiting for OpenEMR to accept connections... (attempt $ATTEMPT/$MAX_ATTEMPTS)"
      sleep 2
    fi
  done

  if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
    echo "ERROR: OpenEMR did not come up after 2 minutes. Check logs with: ssh $SERVER 'cd $REMOTE_DIR && docker compose logs'"
    exit 1
  fi
fi

if [ "$FRESH" = true ]; then
  echo ""
  echo "=== Fresh deploy: Registering OAuth client ==="
  # Source .env.prod to get MYSQL_PASSWORD and OPENEMR_PASS for the registration script
  set -a
  # shellcheck disable=SC1091
  source .env.prod
  set +a
  ./scripts/register-oauth.sh "$HOST"

  echo ""
  echo "=== Saving OAuth credentials back to local .env.prod ==="
  # Pull the updated .env back so future deploys preserve the credentials
  scp "$SERVER:$REMOTE_DIR/.env" .env.prod
  echo "Updated .env.prod with OAuth credentials."
fi

# ---------------------------------------------------------------------------
# Post-deploy: verify the entire stack
# ---------------------------------------------------------------------------
echo ""
echo "=== Post-deploy: verifying deployment ==="
"$(dirname "$0")/verify-deployment.sh" "$HOST" || true

echo ""
echo "=== Deploy complete ==="
echo "OpenEMR:  https://$HOST"
echo "Agent:    Not publicly exposed (use: ssh -L 8000:localhost:8000 $SERVER)"
echo "Jaeger:   Not publicly exposed (use: ssh -L 16686:localhost:16686 $SERVER)"
