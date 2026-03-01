#!/usr/bin/env bash
# register-oauth.sh — Register an OAuth2 client in OpenEMR and configure the agent.
#
# This script:
#   1. Waits for MySQL to be ready
#   2. Enables REST/FHIR APIs and password grant in the globals table
#   2b. Registers the Clinical Assistant sidebar module in the modules table
#   3. Waits for the OAuth endpoint to respond
#   4. Registers a new OAuth2 client with grant_type=password
#   5. Enables the client in the oauth_clients table
#   6. Updates .env on the server with the new credentials
#   7. Restarts the agent container to pick up the new credentials
#
# Usage:
#   ./scripts/register-oauth.sh <server-ip>              # remote (via SSH)
set -euo pipefail

ADMIN_PASS="${OPENEMR_PASS:-pass}"
MYSQL_PASS="${MYSQL_PASSWORD:-openemr}"

if [ $# -lt 1 ]; then
  echo "Usage: $0 <server-ip>"
  exit 1
fi

TARGET="$1"

# ---------------------------------------------------------------------------
# Helper: run a command via SSH on the prod server
# ---------------------------------------------------------------------------
run() {
  ssh -o StrictHostKeyChecking=no "root@$TARGET" "$@"
}

COMPOSE_DIR="/opt/emr-agent"
COMPOSE_CMD="cd $COMPOSE_DIR && docker compose -f docker-compose.prod.yml"

MYSQL_CONTAINER=$(run "$COMPOSE_CMD ps --format '{{.Name}}' mysql" 2>/dev/null | head -1)
OPENEMR_CONTAINER=$(run "$COMPOSE_CMD ps --format '{{.Name}}' openemr" 2>/dev/null | head -1)

if [ -z "$MYSQL_CONTAINER" ] || [ -z "$OPENEMR_CONTAINER" ]; then
  echo "ERROR: Could not find mysql or openemr containers. Are they running?"
  exit 1
fi

echo "MySQL container:   $MYSQL_CONTAINER"
echo "OpenEMR container: $OPENEMR_CONTAINER"

# ---------------------------------------------------------------------------
# Step 1: Wait for MySQL to be ready
# ---------------------------------------------------------------------------
echo ""
echo "=== [1/7] Waiting for MySQL to be ready ==="
for i in $(seq 1 60); do
  if run "docker exec $MYSQL_CONTAINER mysqladmin ping -uopenemr -p'$MYSQL_PASS' --silent" >/dev/null 2>&1; then
    echo "MySQL is ready (attempt $i)"
    break
  fi
  if [ "$i" = "60" ]; then
    echo "ERROR: MySQL not ready after 5 minutes"
    exit 1
  fi
  echo "  Waiting for MySQL... (attempt $i/60)"
  sleep 5
done

# ---------------------------------------------------------------------------
# Step 2: Enable REST/FHIR APIs and password grant
#
# On a fresh install, the APIs are disabled by default (gl_value=0).
# We must enable them BEFORE the OAuth endpoint will respond.
# ---------------------------------------------------------------------------
echo ""
echo "=== [2/7] Enabling REST/FHIR APIs and password grant ==="
# Wait for globals table to exist (OpenEMR creates it during init)
for i in $(seq 1 60); do
  TABLE_EXISTS=$(run "docker exec $MYSQL_CONTAINER mysql -uopenemr -p'$MYSQL_PASS' openemr -sNe \"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='openemr' AND table_name='globals'\"" 2>/dev/null || echo "0")
  if [ "$TABLE_EXISTS" -gt 0 ] 2>/dev/null; then
    echo "globals table exists (attempt $i)"
    break
  fi
  if [ "$i" = "60" ]; then
    echo "ERROR: globals table not found after 5 minutes"
    exit 1
  fi
  echo "  Waiting for OpenEMR schema init... (attempt $i/60)"
  sleep 5
done

run "docker exec $MYSQL_CONTAINER mysql -uopenemr -p'$MYSQL_PASS' openemr -e \"
  UPDATE globals SET gl_value = 1 WHERE gl_name IN (
    'rest_api', 'rest_fhir_api', 'rest_portal_api',
    'rest_system_scopes_api', 'oauth_password_grant'
  );
\""
echo "Done."

# ---------------------------------------------------------------------------
# Step 2b: Register Clinical Assistant sidebar module
#
# The seed_data.sql INSERT for this module fails during docker-entrypoint-initdb.d
# because the modules table doesn't exist yet (OpenEMR creates it during its own
# PHP initialization, which happens AFTER MySQL init scripts run). So we register
# the module here, after OpenEMR has created its schema.
# ---------------------------------------------------------------------------
echo ""
echo "=== [2b/7] Registering Clinical Assistant sidebar module ==="
run "docker exec $MYSQL_CONTAINER mysql -uopenemr -p'$MYSQL_PASS' openemr -e \"
  INSERT INTO modules (
    mod_name, mod_directory, mod_parent, mod_type,
    mod_active, mod_ui_name, mod_relative_link,
    mod_ui_order, mod_ui_active, mod_description,
    mod_nick_name, mod_enc_menu, directory, date,
    sql_run, type, sql_version, acl_version
  ) VALUES (
    'ClinicalAssistant', 'oe-module-clinical-assistant', '', '',
    1, 'Clinical Assistant', '',
    0, 1, 'Clinical Assistant Sidebar',
    '', 'no', '', NOW(),
    0, 0, '', ''
  ) ON DUPLICATE KEY UPDATE mod_active = 1, mod_ui_active = 1;
\""
echo "Done."

# ---------------------------------------------------------------------------
# Step 3: Wait for OpenEMR OAuth endpoint to respond
# ---------------------------------------------------------------------------
echo ""
echo "=== [3/7] Waiting for OpenEMR OAuth endpoint ==="
for i in $(seq 1 60); do
  HTTP_CODE=$(run "curl -sk -o /dev/null -w '%{http_code}' http://localhost:80/oauth2/default/.well-known/openid-configuration" 2>/dev/null || echo "000")
  if [ "$HTTP_CODE" = "200" ]; then
    echo "OAuth endpoint ready (attempt $i)"
    break
  fi
  if [ "$i" = "60" ]; then
    echo "ERROR: OAuth endpoint not ready after 5 minutes"
    exit 1
  fi
  echo "  Waiting... (attempt $i/60, got HTTP $HTTP_CODE)"
  sleep 5
done

# ---------------------------------------------------------------------------
# Step 4: Register OAuth2 client with password grant
# ---------------------------------------------------------------------------
echo ""
echo "=== [4/7] Registering OAuth2 client ==="

# Register from inside the OpenEMR container so the issuer matches http://localhost:80
REGISTRATION_RESPONSE=$(run "docker exec $OPENEMR_CONTAINER curl -s -X POST \
  'http://localhost:80/oauth2/default/registration' \
  -H 'Content-Type: application/json' \
  -d '{
    \"application_type\": \"private\",
    \"client_name\": \"Clinical Agent\",
    \"redirect_uris\": [\"http://localhost:8000/callback\"],
    \"token_endpoint_auth_method\": \"client_secret_post\",
    \"grant_types\": [\"password\"],
    \"contacts\": [\"admin@localhost\"],
    \"scope\": \"openid api:oemr api:fhir user/Patient.read user/Patient.write user/Condition.read user/Observation.read user/MedicationRequest.read user/Medication.read user/Encounter.read user/AllergyIntolerance.read user/Immunization.read user/Procedure.read user/DiagnosticReport.read user/DocumentReference.read user/Organization.read user/Practitioner.read user/CarePlan.read user/CareTeam.read user/Goal.read user/Provenance.read user/Coverage.read user/Device.read user/Location.read user/patient.read user/patient.write user/medical_problem.read user/medical_problem.write user/allergy.read user/allergy.write user/medication.read user/medication.write user/encounter.read user/encounter.write user/vital.read user/vital.write\"
  }'" 2>/dev/null)

CLIENT_ID=$(echo "$REGISTRATION_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['client_id'])" 2>/dev/null)
CLIENT_SECRET=$(echo "$REGISTRATION_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['client_secret'])" 2>/dev/null)

if [ -z "$CLIENT_ID" ] || [ -z "$CLIENT_SECRET" ]; then
  echo "ERROR: Failed to register OAuth client."
  echo "Response: $REGISTRATION_RESPONSE"
  exit 1
fi

echo "Client ID:     $CLIENT_ID"
echo "Client Secret: $CLIENT_SECRET"

# ---------------------------------------------------------------------------
# Step 5: Enable the client in the database
# ---------------------------------------------------------------------------
echo ""
echo "=== [5/7] Enabling OAuth client in database ==="
run "docker exec $MYSQL_CONTAINER mysql -uopenemr -p'$MYSQL_PASS' openemr -e \"
  UPDATE oauth_clients SET is_enabled = 1 WHERE client_name = 'Clinical Agent';
\""
echo "Done."

# ---------------------------------------------------------------------------
# Step 6: Update .env with new credentials
# ---------------------------------------------------------------------------
echo ""
echo "=== [6/7] Updating .env with OAuth credentials ==="
ENV_FILE="/opt/emr-agent/.env"

run "sed -i 's|^OPENEMR_CLIENT_ID=.*|OPENEMR_CLIENT_ID=$CLIENT_ID|' '$ENV_FILE'"
run "sed -i 's|^OPENEMR_CLIENT_SECRET=.*|OPENEMR_CLIENT_SECRET=$CLIENT_SECRET|' '$ENV_FILE'"

# Ensure OPENEMR_USER and OPENEMR_PASS are set
run "grep -q '^OPENEMR_USER=' '$ENV_FILE' || echo 'OPENEMR_USER=admin' >> '$ENV_FILE'"
run "grep -q '^OPENEMR_PASS=' '$ENV_FILE' || echo 'OPENEMR_PASS=$ADMIN_PASS' >> '$ENV_FILE'"

echo "Done."

# ---------------------------------------------------------------------------
# Step 7: Restart agent to pick up new credentials
# ---------------------------------------------------------------------------
echo ""
echo "=== [7/7] Restarting agent container ==="
run "cd /opt/emr-agent && docker compose -f docker-compose.prod.yml --env-file .env up -d agent"

# ---------------------------------------------------------------------------
# Verify token acquisition
# ---------------------------------------------------------------------------
echo ""
echo "=== Verifying OAuth token acquisition ==="
sleep 5

TOKEN_RESPONSE=$(run "curl -s -X POST 'http://localhost:80/oauth2/default/token' \
  -d 'grant_type=password' \
  -d 'username=admin' \
  -d 'password=$ADMIN_PASS' \
  -d 'client_id=$CLIENT_ID' \
  -d 'client_secret=$CLIENT_SECRET' \
  -d 'user_role=users' \
  -d 'scope=openid%20api:oemr%20api:fhir%20user/Patient.read'" 2>/dev/null)

HAS_TOKEN=$(echo "$TOKEN_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print('yes' if 'access_token' in d else 'no')" 2>/dev/null || echo "no")

if [ "$HAS_TOKEN" = "yes" ]; then
  echo "✅ OAuth token acquired successfully!"
else
  echo "❌ OAuth token acquisition FAILED"
  echo "Response: $TOKEN_RESPONSE"
  exit 1
fi

echo ""
echo "=== OAuth registration complete ==="
echo "Client ID:     $CLIENT_ID"
echo "Client Secret: $CLIENT_SECRET"
echo ""
echo "IMPORTANT: Save these credentials! The secret is only available in"
echo "plaintext at registration time. The database stores it encrypted."
