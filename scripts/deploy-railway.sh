#!/usr/bin/env bash
# deploy-railway.sh — Deploy the EMR agent stack to Railway.
#
# Prerequisites:
#   - Railway CLI: npm i -g @railway/cli (or ~/.local/bin/railway)
#   - .env with RAILWAY_API_KEY and ANTHROPIC_API_KEY
#
# Usage: ./scripts/deploy-railway.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

set -a
source .env
set +a
export RAILWAY_API_TOKEN="$RAILWAY_API_KEY"

RAILWAY="${RAILWAY_CLI:-$HOME/.local/bin/railway}"
PROJECT_NAME="emr-agent"

command -v "$RAILWAY" >/dev/null 2>&1 || {
  echo "ERROR: Railway CLI not found at $RAILWAY"
  echo "Install: npm i -g @railway/cli"
  exit 1
}

echo "Logged in as: $($RAILWAY whoami 2>&1)"

# ── 1. Create project ────────────────────────────────────────────
echo ""
echo "=== Creating Railway project: $PROJECT_NAME ==="
$RAILWAY init --name "$PROJECT_NAME" 2>&1 || true

PROJECT_ID=$($RAILWAY status --json 2>&1 | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Project ID: $PROJECT_ID"

# ── 2. Add MySQL ─────────────────────────────────────────────────
echo ""
echo "=== Adding MySQL database ==="
$RAILWAY add --database mysql 2>&1 || true

# ── 3. Agent service ─────────────────────────────────────────────
echo ""
echo "=== Setting up agent service ==="
$RAILWAY add --service agent 2>&1 || true
$RAILWAY link --project "$PROJECT_ID" --service agent --environment production 2>&1

$RAILWAY variables set ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" 2>&1
$RAILWAY variables set OPENEMR_BASE_URL="http://openemr.railway.internal" 2>&1
$RAILWAY variables set OPENEMR_FHIR_URL="http://openemr.railway.internal/apis/default/fhir" 2>&1
$RAILWAY variables set OPENEMR_CLIENT_ID="placeholder" 2>&1
$RAILWAY variables set OPENEMR_CLIENT_SECRET="placeholder" 2>&1
$RAILWAY variables set OTEL_EXPORTER_OTLP_ENDPOINT="http://jaeger.railway.internal:4317" 2>&1
$RAILWAY variables set SESSION_DB_PATH="data/sessions.db" 2>&1
$RAILWAY variables set PORT="8000" 2>&1

echo "Deploying agent..."
$RAILWAY up --detach 2>&1
$RAILWAY domain 2>&1 || true

# ── 4. OpenEMR service ───────────────────────────────────────────
echo ""
echo "=== Setting up OpenEMR service ==="
$RAILWAY add --service openemr --image "openemr/openemr:flex" 2>&1 || true
$RAILWAY link --project "$PROJECT_ID" --service openemr --environment production 2>&1

$RAILWAY variables set OE_USER="admin" 2>&1
$RAILWAY variables set OE_PASS="pass" 2>&1
$RAILWAY variables set MANUAL_SETUP="0" 2>&1
$RAILWAY variables set DEMO_MODE="standard" 2>&1
$RAILWAY variables set OPENEMR_AGENT_API_URL="http://agent.railway.internal:8000" 2>&1
$RAILWAY variables set FLEX_REPOSITORY="https://github.com/stephenchilcote-gauntlet/openemr.git" 2>&1
$RAILWAY variables set FLEX_REPOSITORY_BRANCH="master" 2>&1
$RAILWAY variables set PORT="80" 2>&1
# MySQL references (Railway resolves these)
$RAILWAY variables set 'MYSQL_HOST=${{MySQL.MYSQLHOST}}' 2>&1
$RAILWAY variables set 'MYSQL_ROOT_PASS=${{MySQL.MYSQLPASSWORD}}' 2>&1
$RAILWAY variables set 'MYSQL_USER=${{MySQL.MYSQLUSER}}' 2>&1
$RAILWAY variables set 'MYSQL_PASS=${{MySQL.MYSQLPASSWORD}}' 2>&1

$RAILWAY domain 2>&1 || true

# ── 5. Jaeger service ────────────────────────────────────────────
echo ""
echo "=== Setting up Jaeger service ==="
$RAILWAY add --service jaeger --image "jaegertracing/jaeger:latest" \
  --variables "COLLECTOR_OTLP_ENABLED=true" 2>&1 || true
$RAILWAY link --project "$PROJECT_ID" --service jaeger --environment production 2>&1

$RAILWAY variables set PORT="16686" 2>&1
$RAILWAY domain 2>&1 || true

# ── Summary ───────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  All services deployed!"
echo "============================================================"
echo ""
echo "OpenEMR takes ~5 min on first boot to clone repo and init DB."
echo ""
echo "After OpenEMR is up:"
echo "  1. Seed test data: ./scripts/railway-seed.sh"
echo "  2. Register OAuth2 client at <openemr-domain>/oauth2/default/registration"
echo "  3. Update OPENEMR_CLIENT_ID / OPENEMR_CLIENT_SECRET on agent service"
echo ""
echo "Dashboard: $RAILWAY open"
