#!/usr/bin/env bash
# preflight-check.sh — Pre-deploy validation of .env.prod.
#
# Catches every "Deployment Bug Hall of Fame" entry that can be detected
# locally BEFORE deploying.  Run this before deploy.sh.
#
# Usage:
#   ./scripts/preflight-check.sh <server-ip>            # normal deploy preflight
#   ./scripts/preflight-check.sh <server-ip> --fresh     # fresh deploy (allows empty OAuth creds)
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
  echo "Usage: $0 <server-ip> [--fresh]"
  exit 1
fi

HOST="${1#https://}"; HOST="${HOST#http://}"; HOST="${HOST%/}"
SERVER="root@$HOST"
FRESH=false
[ "${2:-}" = "--fresh" ] && FRESH=true

ENV_FILE=".env.prod"
if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: $ENV_FILE not found. Cannot preflight."
  exit 1
fi

# ---------------------------------------------------------------------------
# Safe .env parser (never source untrusted files)
# ---------------------------------------------------------------------------
dotenv_get() {
  python3 -c "
import sys
key = sys.argv[1]
val = None
for raw in open('$ENV_FILE', 'r').read().splitlines():
    line = raw.strip()
    if not line or line.startswith('#'): continue
    if line.startswith('export '): line = line[7:].lstrip()
    if '=' not in line: continue
    k, v = line.split('=', 1)
    k = k.strip()
    if k == key:
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('\"', \"'\"):
            v = v[1:-1]
        val = v
print('' if val is None else val)
" "$1" 2>/dev/null
}

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║    Pre-Deploy Preflight Check            ║"
echo "╚══════════════════════════════════════════╝"
echo "  Target: $HOST  Fresh: $FRESH"
echo ""

# ---------------------------------------------------------------------------
# 1. Required variables present
# ---------------------------------------------------------------------------
echo "── .env.prod contents ──"

REQUIRED_ALWAYS=(MYSQL_ROOT_PASSWORD MYSQL_PASSWORD OPENEMR_ADMIN_PASS ANTHROPIC_API_KEY)
for key in "${REQUIRED_ALWAYS[@]}"; do
  val=$(dotenv_get "$key")
  if [ -z "$val" ]; then
    check_fail "$key is missing or empty"
  else
    check_pass "$key is set"
  fi
done

# ---------------------------------------------------------------------------
# 2. OPENEMR_PASS must be exactly 'pass' (DEMO_MODE quirk)
#    This is bug #1 in the Hall of Fame — hit 3+ times.
# ---------------------------------------------------------------------------
echo ""
echo "── OAuth password invariant (Hall of Fame #1: invalid_grant) ──"

OE_PASS=$(dotenv_get "OPENEMR_PASS")
if [ "$OE_PASS" = "pass" ]; then
  check_pass "OPENEMR_PASS is exactly 'pass'"
elif [ -z "$OE_PASS" ]; then
  check_fail "OPENEMR_PASS is missing — must be explicitly set to 'pass'"
  echo "         DEMO_MODE=standard ignores OE_PASS for OAuth; password is always 'pass'"
else
  check_fail "OPENEMR_PASS is '$OE_PASS' — MUST be 'pass' (DEMO_MODE quirk)"
  echo "         This causes 'invalid_grant' every time. Change it to: OPENEMR_PASS=pass"
fi

# ---------------------------------------------------------------------------
# 3. OAuth credentials (Hall of Fame #4: deploy overwrites working .env)
# ---------------------------------------------------------------------------
echo ""
echo "── OAuth credentials (Hall of Fame #4: .env overwrite) ──"

LOCAL_CLIENT_ID=$(dotenv_get "OPENEMR_CLIENT_ID")
LOCAL_CLIENT_SECRET=$(dotenv_get "OPENEMR_CLIENT_SECRET")

if [ "$FRESH" = true ]; then
  # Fresh deploy: empty OAuth creds are expected (register-oauth.sh will fill them)
  if [ -z "$LOCAL_CLIENT_ID" ] && [ -z "$LOCAL_CLIENT_SECRET" ]; then
    check_pass "OAuth creds empty (expected for --fresh, register-oauth.sh will populate)"
  elif [ -n "$LOCAL_CLIENT_ID" ] && [ -n "$LOCAL_CLIENT_SECRET" ]; then
    check_warn "OAuth creds present in .env.prod but --fresh will re-register new ones"
  else
    check_warn "Only one OAuth credential set — --fresh will replace both anyway"
  fi
else
  # Normal deploy: OAuth creds are required locally
  if [ -z "$LOCAL_CLIENT_ID" ] || [ -z "$LOCAL_CLIENT_SECRET" ]; then
    check_fail "OAuth creds missing in .env.prod — deploy will overwrite server's working .env"
    echo "         Fix: scp $SERVER:/opt/emr-agent/.env .env.prod"
    echo "         Or use --fresh to re-register OAuth"
  else
    check_pass "Local OAuth creds present (ID: ${LOCAL_CLIENT_ID:0:8}…)"

    # Compare against remote to catch divergence
    REMOTE_CLIENT_ID=$(ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "$SERVER" \
      "grep -E '^OPENEMR_CLIENT_ID=' /opt/emr-agent/.env 2>/dev/null | tail -1 | cut -d= -f2-" 2>/dev/null || echo "")

    if [ -n "$REMOTE_CLIENT_ID" ] && [ "$REMOTE_CLIENT_ID" != "$LOCAL_CLIENT_ID" ]; then
      check_fail "Local OAuth CLIENT_ID differs from remote — deploy would overwrite working creds"
      echo "         Local:  ${LOCAL_CLIENT_ID:0:8}…"
      echo "         Remote: ${REMOTE_CLIENT_ID:0:8}…"
      echo "         Fix: scp $SERVER:/opt/emr-agent/.env .env.prod"
    elif [ -n "$REMOTE_CLIENT_ID" ]; then
      check_pass "Local OAuth creds match remote"
    else
      check_warn "Could not read remote .env (server unreachable or no prior deploy)"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 4. OPENEMR_USER should be set
# ---------------------------------------------------------------------------
echo ""
echo "── Other checks ──"

OE_USER=$(dotenv_get "OPENEMR_USER")
if [ -z "$OE_USER" ]; then
  check_warn "OPENEMR_USER not set — will default to 'admin' (probably fine)"
else
  check_pass "OPENEMR_USER is '$OE_USER'"
fi

# ---------------------------------------------------------------------------
# 5. No CRLF line endings (causes subtle bash parse failures)
# ---------------------------------------------------------------------------
if grep -cP '\r' "$ENV_FILE" > /dev/null 2>&1; then
  check_fail ".env.prod has Windows line endings (CRLF) — will cause bash parse errors"
  echo "         Fix: sed -i 's/\\r$//' .env.prod"
else
  check_pass ".env.prod has Unix line endings"
fi

# ---------------------------------------------------------------------------
# 6. No spaces around = (causes key='key ' or value=' value')
# ---------------------------------------------------------------------------
BAD_SPACES=$(grep -nE '^[A-Z_]+ +=' "$ENV_FILE" || true)
if [ -n "$BAD_SPACES" ]; then
  check_fail "Spaces before = sign in .env.prod:"
  echo "$BAD_SPACES" | sed 's/^/         /'
else
  check_pass "No malformed key=value lines"
fi

echo ""
finish
