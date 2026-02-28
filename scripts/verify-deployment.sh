#!/usr/bin/env bash
# verify-deployment.sh — Post-deploy verification of the entire EMR agent stack.
#
# Verifies every link in the chain that has historically broken. Read-only,
# idempotent, safe to run at any time.
#
# Usage:
#   ./scripts/verify-deployment.sh <server-ip>    # remote (via SSH)
#   ./scripts/verify-deployment.sh local           # local docker compose
#
# Exit code: 0 if all checks pass, 1 if any fail.
set -uo pipefail

PASS=0; FAIL=0; WARN=0
check_pass() { printf "  \033[32m[PASS]\033[0m %s\n" "$1"; PASS=$((PASS+1)); }
check_fail() { printf "  \033[31m[FAIL]\033[0m %s\n" "$1"; FAIL=$((FAIL+1)); }
check_warn() { printf "  \033[33m[WARN]\033[0m %s\n" "$1"; WARN=$((WARN+1)); }
finish() {
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  printf "  Passed: %d   Failed: %d   Warnings: %d\n" "$PASS" "$FAIL" "$WARN"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  [ "$FAIL" -eq 0 ]
}

if [ $# -lt 1 ]; then
  echo "Usage: $0 <server-ip|local>"
  exit 1
fi

TARGET="$1"

# ---------------------------------------------------------------------------
# Helper: run a command locally or via SSH
# ---------------------------------------------------------------------------
run() {
  if [ "$TARGET" = "local" ]; then
    eval "$@"
  else
    ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "root@$TARGET" "$@"
  fi
}

# ---------------------------------------------------------------------------
# Determine compose context and container names
# ---------------------------------------------------------------------------
if [ "$TARGET" = "local" ]; then
  COMPOSE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
  COMPOSE_CMD="cd $COMPOSE_DIR && docker compose"
else
  COMPOSE_DIR="/opt/emr-agent"
  COMPOSE_CMD="cd $COMPOSE_DIR && docker compose -f docker-compose.prod.yml"
fi

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Post-Deploy Verification Check         ║"
echo "╚══════════════════════════════════════════╝"
echo "  Target: $TARGET"
echo ""

# ---------------------------------------------------------------------------
# Step 0: Find containers
# ---------------------------------------------------------------------------
echo "── Containers ──"

MYSQL_CONTAINER=$(run "$COMPOSE_CMD ps --format '{{.Name}}' mysql" 2>/dev/null | head -1)
OPENEMR_CONTAINER=$(run "$COMPOSE_CMD ps --format '{{.Name}}' openemr" 2>/dev/null | head -1)
AGENT_CONTAINER=$(run "$COMPOSE_CMD ps --format '{{.Name}}' agent" 2>/dev/null | head -1)

[ -n "$MYSQL_CONTAINER" ] && check_pass "MySQL container: $MYSQL_CONTAINER" || check_fail "MySQL container not found"
[ -n "$OPENEMR_CONTAINER" ] && check_pass "OpenEMR container: $OPENEMR_CONTAINER" || check_fail "OpenEMR container not found"
[ -n "$AGENT_CONTAINER" ] && check_pass "Agent container: $AGENT_CONTAINER" || check_fail "Agent container not found"

if [ -z "$MYSQL_CONTAINER" ] || [ -z "$OPENEMR_CONTAINER" ] || [ -z "$AGENT_CONTAINER" ]; then
  echo ""
  echo "  Cannot continue without all containers running."
  finish
  exit 1
fi

# Read .env for credentials (redact in output)
ENV_FILE="$COMPOSE_DIR/.env"
env_get() {
  run "grep -E '^${1}=' '$ENV_FILE' 2>/dev/null | tail -1 | cut -d= -f2-" 2>/dev/null || echo ""
}

MYSQL_PASS=$(env_get "MYSQL_PASSWORD")
OE_PASS=$(env_get "OPENEMR_PASS")
CLIENT_ID=$(env_get "OPENEMR_CLIENT_ID")
CLIENT_SECRET=$(env_get "OPENEMR_CLIENT_SECRET")
OE_USER=$(env_get "OPENEMR_USER")
[ -z "$OE_USER" ] && OE_USER="admin"

# ---------------------------------------------------------------------------
# Step 1: MySQL connectivity
# ---------------------------------------------------------------------------
echo ""
echo "── MySQL (Hall of Fame prerequisite) ──"

if run "docker exec $MYSQL_CONTAINER mysqladmin ping -uopenemr -p'$MYSQL_PASS' --silent" >/dev/null 2>&1; then
  check_pass "MySQL is accepting connections"
else
  check_fail "MySQL ping failed — check MYSQL_PASSWORD"
fi

# ---------------------------------------------------------------------------
# Step 2: REST/FHIR APIs enabled in globals (Hall of Fame #3)
# ---------------------------------------------------------------------------
echo ""
echo "── API Globals (Hall of Fame #3: REST APIs not enabled) ──"

REQUIRED_GLOBALS=(rest_api rest_fhir_api rest_portal_api rest_system_scopes_api oauth_password_grant)
for gl in "${REQUIRED_GLOBALS[@]}"; do
  VAL=$(run "docker exec $MYSQL_CONTAINER mysql -uopenemr -p'$MYSQL_PASS' openemr -sNe \
    \"SELECT gl_value FROM globals WHERE gl_name='$gl'\"" 2>/dev/null || echo "")
  if [ "$VAL" = "1" ]; then
    check_pass "$gl = 1"
  elif [ -z "$VAL" ]; then
    check_fail "$gl not found in globals table (OpenEMR may still be initializing)"
  else
    check_fail "$gl = $VAL (must be 1)"
    echo "         Fix: UPDATE globals SET gl_value=1 WHERE gl_name='$gl';"
  fi
done

# ---------------------------------------------------------------------------
# Step 3: OAuth client registered and enabled (Hall of Fame #2)
# ---------------------------------------------------------------------------
echo ""
echo "── OAuth Client (Hall of Fame #2: is_enabled=0) ──"

if [ -z "$CLIENT_ID" ]; then
  check_fail "OPENEMR_CLIENT_ID is empty in .env — OAuth not configured"
else
  check_pass "OPENEMR_CLIENT_ID is set (${CLIENT_ID:0:8}…)"

  IS_ENABLED=$(run "docker exec $MYSQL_CONTAINER mysql -uopenemr -p'$MYSQL_PASS' openemr -sNe \
    \"SELECT is_enabled FROM oauth_clients WHERE client_id='$CLIENT_ID'\"" 2>/dev/null || echo "")

  if [ "$IS_ENABLED" = "1" ]; then
    check_pass "OAuth client is_enabled = 1"
  elif [ "$IS_ENABLED" = "0" ]; then
    check_fail "OAuth client is_enabled = 0 — token requests will fail"
    echo "         Fix: UPDATE oauth_clients SET is_enabled=1 WHERE client_id='$CLIENT_ID';"
  elif [ -z "$IS_ENABLED" ]; then
    check_fail "OAuth client not found in oauth_clients table"
    echo "         Fix: Re-run register-oauth.sh or deploy --fresh"
  fi

  GRANT_TYPES=$(run "docker exec $MYSQL_CONTAINER mysql -uopenemr -p'$MYSQL_PASS' openemr -sNe \
    \"SELECT grant_types FROM oauth_clients WHERE client_id='$CLIENT_ID'\"" 2>/dev/null || echo "")

  if echo "$GRANT_TYPES" | grep -q "password"; then
    check_pass "OAuth client has password grant type"
  else
    check_fail "OAuth client missing password grant type: $GRANT_TYPES"
  fi
fi

# ---------------------------------------------------------------------------
# Step 4: OPENEMR_PASS invariant (Hall of Fame #1: invalid_grant)
# ---------------------------------------------------------------------------
echo ""
echo "── OAuth Password (Hall of Fame #1: invalid_grant) ──"

if [ "$OE_PASS" = "pass" ]; then
  check_pass "OPENEMR_PASS is 'pass' in server .env"
elif [ -z "$OE_PASS" ]; then
  check_warn "OPENEMR_PASS not in .env — will default to 'pass' (should be explicit)"
else
  check_fail "OPENEMR_PASS is '$OE_PASS' — MUST be 'pass' (DEMO_MODE quirk)"
fi

# ---------------------------------------------------------------------------
# Step 5: Token acquisition — the ultimate OAuth truth test
# ---------------------------------------------------------------------------
echo ""
echo "── Token Acquisition (proves OAuth works end-to-end) ──"

if [ -n "$CLIENT_ID" ] && [ -n "$CLIENT_SECRET" ]; then
  TOKEN_RESPONSE=$(run "curl -sf -X POST 'http://localhost:80/oauth2/default/token' \
    -d 'grant_type=password' \
    -d 'username=$OE_USER' \
    -d 'password=${OE_PASS:-pass}' \
    -d 'client_id=$CLIENT_ID' \
    -d 'client_secret=$CLIENT_SECRET' \
    -d 'user_role=users' \
    -d 'scope=openid%20api:oemr%20api:fhir%20user/Patient.read'" 2>/dev/null || echo "")

  HAS_TOKEN=$(echo "$TOKEN_RESPONSE" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print('yes' if 'access_token' in d else 'no')
except: print('no')
" 2>/dev/null || echo "no")

  if [ "$HAS_TOKEN" = "yes" ]; then
    check_pass "OAuth token acquired successfully"
  else
    check_fail "OAuth token acquisition FAILED"
    # Extract error safely
    ERROR_MSG=$(echo "$TOKEN_RESPONSE" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('error', '') + ': ' + d.get('error_description', d.get('hint', '')))
except: print(sys.stdin.read()[:200])
" 2>/dev/null || echo "(no response)")
    echo "         Error: $ERROR_MSG"
  fi
else
  check_fail "Cannot test token — CLIENT_ID or CLIENT_SECRET missing from .env"
fi

# ---------------------------------------------------------------------------
# Step 6: Agent health (proves agent is running and connected to OpenEMR)
# ---------------------------------------------------------------------------
echo ""
echo "── Agent Health ──"

HEALTH_RESPONSE=$(run "curl -sf http://localhost:8000/api/health" 2>/dev/null || echo "")
if [ -n "$HEALTH_RESPONSE" ]; then
  check_pass "Agent API is responding on :8000"

  CONNECTED=$(echo "$HEALTH_RESPONSE" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print('yes' if d.get('openemr_connected') else 'no')
except: print('no')
" 2>/dev/null || echo "no")

  if [ "$CONNECTED" = "yes" ]; then
    check_pass "Agent reports openemr_connected: true"
  else
    check_fail "Agent reports openemr_connected: false — OAuth or network issue"
    echo "         Response: $HEALTH_RESPONSE"
  fi
else
  check_fail "Agent API not responding on :8000"
  echo "         Check: docker logs $AGENT_CONTAINER --tail 20"
fi

# ---------------------------------------------------------------------------
# Step 7: Sidebar module registered (Hall of Fame #5)
# ---------------------------------------------------------------------------
echo ""
echo "── Sidebar Module (Hall of Fame #5: module not in table) ──"

MOD_ACTIVE=$(run "docker exec $MYSQL_CONTAINER mysql -uopenemr -p'$MYSQL_PASS' openemr -sNe \
  \"SELECT CONCAT(mod_active, ',', mod_ui_active) FROM modules WHERE mod_name='ClinicalAssistant'\"" 2>/dev/null || echo "")

if [ "$MOD_ACTIVE" = "1,1" ]; then
  check_pass "ClinicalAssistant module: mod_active=1, mod_ui_active=1"
elif [ -z "$MOD_ACTIVE" ]; then
  check_fail "ClinicalAssistant not in modules table"
  echo "         Fix: Re-run register-oauth.sh (step 2b registers it)"
else
  check_fail "ClinicalAssistant module flags: $MOD_ACTIVE (need 1,1)"
fi

# ---------------------------------------------------------------------------
# Step 8: Apache reverse proxy (Hall of Fame #6 + #8)
# ---------------------------------------------------------------------------
echo ""
echo "── Apache Proxy (Hall of Fame #6: proxy not configured, #8: stale IP) ──"

# Check env var (must be Docker service name, not IP)
AGENT_URL=$(run "docker exec $OPENEMR_CONTAINER printenv OPENEMR_AGENT_API_URL" 2>/dev/null || echo "")
if [ -n "$AGENT_URL" ]; then
  if echo "$AGENT_URL" | grep -qE 'http://(172\.|192\.168\.|10\.)'; then
    check_fail "OPENEMR_AGENT_API_URL uses hardcoded IP: $AGENT_URL"
    echo "         Must use Docker service name: http://agent:8000"
  else
    check_pass "OPENEMR_AGENT_API_URL: $AGENT_URL (no hardcoded IP)"
  fi
else
  check_warn "OPENEMR_AGENT_API_URL not set (defaults to http://agent:8000)"
fi

# Behavioral check: can OpenEMR reach agent via Docker DNS?
INTER_CONTAINER=$(run "docker exec $OPENEMR_CONTAINER curl -sf -o /dev/null -w '%{http_code}' http://agent:8000/api/health" 2>/dev/null || echo "000")
if [ "$INTER_CONTAINER" = "200" ]; then
  check_pass "OpenEMR → agent inter-container connectivity works"
else
  check_fail "OpenEMR cannot reach agent at http://agent:8000 (HTTP $INTER_CONTAINER)"
fi

# Behavioral check: proxy path works end-to-end
PROXY_STATUS=$(run "curl -sf -o /dev/null -w '%{http_code}' http://localhost/agent-api/api/health" 2>/dev/null || echo "000")
if [ "$PROXY_STATUS" = "200" ]; then
  check_pass "/agent-api/ proxy returns 200"
else
  check_fail "/agent-api/ proxy returns HTTP $PROXY_STATUS (expected 200)"
  echo "         The Apache ProxyPass rule may be missing. Rebuild OpenEMR container."
fi

# Sidebar iframe URL
UI_STATUS=$(run "curl -sf -o /dev/null -w '%{http_code}' http://localhost/agent-api/ui" 2>/dev/null || echo "000")
if [ "$UI_STATUS" = "200" ]; then
  check_pass "/agent-api/ui (sidebar iframe) returns 200"
else
  check_fail "/agent-api/ui returns HTTP $UI_STATUS — sidebar will show 404"
fi

# ---------------------------------------------------------------------------
# Step 9: SSL certs (Hall of Fame #7 — already fixed but verify)
# ---------------------------------------------------------------------------
echo ""
echo "── TLS Certificates ──"

if [ "$TARGET" != "local" ]; then
  CERT_EXISTS=$(run "test -f /etc/letsencrypt/live/emragent.404.mn/fullchain.pem && echo yes || echo no" 2>/dev/null || echo "no")
  if [ "$CERT_EXISTS" = "yes" ]; then
    check_pass "Let's Encrypt cert exists on host filesystem (immune to docker down -v)"
  else
    check_warn "No Let's Encrypt cert found — using self-signed (OK for dev)"
  fi
else
  check_pass "Local deploy — TLS check skipped"
fi

echo ""
finish
