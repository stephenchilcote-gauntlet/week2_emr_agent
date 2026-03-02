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
  src/ web/ openemr-module/ 2>/dev/null || true)

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
  --exclude 'openemr_fresh/'   # local full-source clone, never upload (use openemr/ for --all)
)

if [ "$DEPLOY_ALL" = true ]; then
  # Full sync includes openemr_fresh/ (~350 MB) — only needed for OpenEMR/sidebar changes
  RSYNC_EXCLUDES+=(
    --exclude 'openemr_fresh/.git'
    --exclude 'openemr_fresh/tests'
    --exclude 'openemr_fresh/.github'
    --exclude 'openemr_fresh/ci'
    --exclude 'openemr_fresh/Documentation'
    --exclude 'openemr_fresh/.phpstan'
  )
  SERVICES=""  # all services
  echo "=== Full sync (including openemr_fresh/) to $SERVER:$REMOTE_DIR ==="
else
  # Agent-only: skip openemr_fresh/ and openemr-module/ entirely
  RSYNC_EXCLUDES+=(--exclude 'openemr_fresh/' --exclude 'openemr-module/')
  SERVICES="agent"
  echo "=== Agent-only sync (skipping openemr_fresh/) to $SERVER:$REMOTE_DIR ==="
fi

# --copy-unsafe-links: dereference only symlinks whose targets fall OUTSIDE the
# transfer root (e.g. absolute paths like /home/login/.../web/sidebar/foo.js).
# Relative in-tree symlinks (openemr/node_modules/.bin/*) are preserved as-is.
# -L (--copy-links) would dereference everything and break node_modules/.bin/.
rsync -avz --copy-unsafe-links --delete \
  "${RSYNC_EXCLUDES[@]}" \
  ./ "$SERVER:$REMOTE_DIR/"

echo "=== Copying .env.prod to server ==="
scp .env.prod "$SERVER:$REMOTE_DIR/.env"

if [ -n "$SERVICES" ]; then
  echo "=== Building and restarting: $SERVICES ==="
  # Separate build from up to guarantee that only the specified service image is
  # rebuilt.  `up --build` passes all services to docker buildx bake which
  # rebuilds ALL images (even from cache), producing a new attestation manifest →
  # new image hash → compose recreates openemr on the next `up` → 2-min boot →
  # OpenEMR crash loop if the build context is missing openemr source files.
  # With an explicit `build <service>` first, bake only touches that one image.
  # shellcheck disable=SC2086
  ssh -o StrictHostKeyChecking=no "$SERVER" "cd $REMOTE_DIR && docker compose -f docker-compose.prod.yml --env-file .env build $SERVICES && docker compose -f docker-compose.prod.yml --env-file .env up -d --no-deps $SERVICES"

  echo "=== Waiting for agent to become healthy ==="
  AGENT_UP=false
  for _i in $(seq 1 30); do
    CONNECTED=$(ssh -o StrictHostKeyChecking=no "$SERVER" \
      "curl -sf http://localhost:8000/api/health 2>/dev/null | python3 -c 'import sys,json; d=json.loads(sys.stdin.read()); print(\"yes\" if d.get(\"openemr_connected\") else \"no\")' 2>/dev/null || echo no") || true
    if [ "$CONNECTED" = "yes" ]; then
      echo "Agent is up and connected to OpenEMR."
      AGENT_UP=true
      break
    fi
    printf "  not ready yet (%d/30)…\n" "$_i"
    sleep 2
  done
  [ "$AGENT_UP" = "true" ] || echo "WARNING: agent did not report openemr_connected=true within 60 s"
else
  echo "=== Building and starting all services ==="
  ssh -o StrictHostKeyChecking=no "$SERVER" "cd $REMOTE_DIR && docker compose -f docker-compose.prod.yml --env-file .env up -d --build"

  echo "=== Waiting for OpenEMR OAuth endpoint to be ready ==="
  # Polling the OpenID config endpoint is more reliable than TCP port 80:
  # Apache accepts TCP connections before PHP/OpenEMR finishes initializing,
  # so a TCP check can pass 60-120 s before OAuth actually works.
  MAX_ATTEMPTS=120
  ATTEMPT=0
  while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    HTTP=$(ssh -o StrictHostKeyChecking=no "$SERVER" \
      "curl -sk -o /dev/null -w '%{http_code}' http://localhost/oauth2/default/.well-known/openid-configuration" \
      2>/dev/null || echo "000")
    if [ "$HTTP" = "200" ]; then
      echo "OpenEMR OAuth endpoint is ready."
      break
    fi
    ATTEMPT=$((ATTEMPT + 1))
    if [ $ATTEMPT -lt $MAX_ATTEMPTS ]; then
      printf "  Waiting for OpenEMR OAuth... HTTP %s (attempt %d/%d)\n" "$HTTP" "$ATTEMPT" "$MAX_ATTEMPTS"
      sleep 5
    fi
  done

  if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
    echo "ERROR: OpenEMR OAuth endpoint did not become ready after 10 minutes."
    echo "Check logs with: ssh $SERVER 'cd $REMOTE_DIR && docker compose -f docker-compose.prod.yml logs openemr --tail 50'"
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

  echo ""
  echo "=== Seeding synthetic patients ==="
  # reset_patients.py uses docker exec against the OpenEMR container (mariadb client).
  # It only needs python-dotenv; everything else is stdlib.  The prod container name
  # follows docker-compose naming: <project>-<service>-<n> where project = dir name.
  REMOTE_PROJECT=$(ssh -o StrictHostKeyChecking=no "$SERVER" "basename $REMOTE_DIR")
  OPENEMR_CTR="${REMOTE_PROJECT}-openemr-1"
  ssh -o StrictHostKeyChecking=no "$SERVER" \
    "apt-get install -y python3-venv -q >/dev/null 2>&1 && \
     python3 -m venv /tmp/emr-seed-venv && \
     /tmp/emr-seed-venv/bin/pip install python-dotenv -q && \
     cd $REMOTE_DIR && \
     OPENEMR_CONTAINER=$OPENEMR_CTR \
     OPENEMR_DB_USER=openemr \
     OPENEMR_DB_PASS=\$(grep '^MYSQL_PASSWORD=' .env | cut -d= -f2-) \
     OPENEMR_DB_NAME=openemr \
     /tmp/emr-seed-venv/bin/python3 scripts/reset_patients.py"
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
