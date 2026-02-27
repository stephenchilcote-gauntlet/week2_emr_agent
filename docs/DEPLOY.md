# Deployment Guide — EMR Agent on Hetzner VPS

**VPS:** https://emragent.404.mn/ (77.42.17.207)

## Quick Reference

```bash
# Agent-only deploy (skips 2.1GB openemr/ sync + OpenEMR restart, ~1 min):
./scripts/deploy.sh 77.42.17.207

# Full deploy (syncs everything, rebuilds all containers):
./scripts/deploy.sh 77.42.17.207 --all

# Full wipe and rebuild (new DB, new OAuth client):
./scripts/deploy.sh 77.42.17.207 --fresh

# Register OAuth on an already-running stack:
MYSQL_PASSWORD=<mysql-pw> ./scripts/register-oauth.sh 77.42.17.207

# SSH tunnels for internal services:
ssh -L 8000:localhost:8000 root@77.42.17.207   # Agent API
ssh -L 16686:localhost:16686 root@77.42.17.207  # Jaeger UI
```

## Architecture

```
Internet ──→ :80/:443 ──→ OpenEMR (Apache+PHP)
                              │
                              ├──→ MySQL 8.0 (emr-net)
                              │
                              ├──→ /agent-api/* ──→ Agent (FastAPI, http://agent:8000)
                              │
              127.0.0.1:8000 ──→ Agent (direct, SSH tunnel only)
                              │
              127.0.0.1:16686 ──→ Jaeger (tracing)
```

- **OpenEMR** is the only publicly exposed service (ports 80/443)
- **Agent API** is proxied through OpenEMR at `/agent-api/*` (for the sidebar)
- **Agent API** is also on `127.0.0.1:8000` for direct access via SSH tunnel
- **Jaeger UI** is on `127.0.0.1:16686` only — access via SSH tunnel
- All services communicate via the `emr-net` Docker bridge network

## Sidebar Integration

The Clinical Assistant sidebar is embedded into the OpenEMR UI. Three things
must all be working for it to appear:

### 1. Module registered in database

The `ClinicalAssistant` module must exist in the `modules` table with
`mod_active=1` and `mod_ui_active=1`. This is registered by
`register-oauth.sh` (step 2b) during `--fresh` deploys.

**Why not `seed_data.sql`?** The seed SQL runs as
`docker-entrypoint-initdb.d/99-seed.sql` during MySQL initialization, but the
`modules` table is created by OpenEMR during its PHP boot — which happens
AFTER MySQL init. So the INSERT silently fails. The registration was moved to
`register-oauth.sh` which runs after OpenEMR has fully booted.

Check: `SELECT mod_name, mod_active FROM modules WHERE mod_name = 'ClinicalAssistant'`

### 2. Bootstrap injects embed.js

When the module is active, OpenEMR loads
`openemr.bootstrap.php` from `oe-module-clinical-assistant/`. This uses an
output buffer to inject a `<script>` tag for `embed.js` before `</body>` on
every page. The `Bootstrap.php` class also hooks the `StyleFilterEvent` as a
second injection path.

The `embed.js` script creates a fixed sidebar `<div>` with an `<iframe>`
pointing to `/agent-api/ui`.

### 3. Apache reverse proxy for `/agent-api/`

The `<iframe>` loads from `/agent-api/ui`, which Apache must proxy to the
agent container at `http://agent:8000/`. This proxy is configured by
`docker/start.sh` at container startup — it loads `mod_proxy` and
`mod_proxy_http`, then adds `ProxyPass /agent-api/ http://agent:8000/` to both
the HTTP and HTTPS vhosts.

The `OPENEMR_AGENT_API_URL` env var (default `http://agent:8000`) controls
the backend URL.

## The `.env.prod` File

This is the **single source of truth** for all deployment credentials. The
deploy script copies it to the server as `.env`. It contains:

| Variable | Purpose | Notes |
|---|---|---|
| `MYSQL_ROOT_PASSWORD` | MySQL root password | |
| `MYSQL_PASSWORD` | MySQL `openemr` user password | |
| `OPENEMR_ADMIN_PASS` | OpenEMR admin login (web UI) | Used by `OE_PASS` in docker-compose |
| `ANTHROPIC_API_KEY` | Claude API key | |
| `ANTHROPIC_MODEL` | Claude model ID | |
| `OPENEMR_CLIENT_ID` | OAuth2 client ID | **Auto-populated by `--fresh` deploy** |
| `OPENEMR_CLIENT_SECRET` | OAuth2 client secret | **Auto-populated by `--fresh` deploy** |
| `OPENEMR_USER` | Agent's OpenEMR username | Default: `admin` |
| `OPENEMR_PASS` | Agent's OpenEMR password for OAuth | **Must be `pass`** — see below |

### ⚠️ CRITICAL: `OPENEMR_PASS` must be `pass`

The OpenEMR admin password for the web UI (`OPENEMR_ADMIN_PASS` / `OE_PASS`)
and the OAuth password grant credential (`OPENEMR_PASS`) are **different**.

- `OPENEMR_ADMIN_PASS` — sets the admin password in the web UI during first boot
- `OPENEMR_PASS` — the password the agent uses for OAuth2 password grant

With `DEMO_MODE: standard` in docker-compose, OpenEMR keeps `pass` as the
OAuth credential regardless of what `OE_PASS` is set to. **Always use
`OPENEMR_PASS=pass`.**

## OAuth2 Setup — How It Works

This is the thing that breaks every time. Here's what happens and why.

### The Chicken-and-Egg Problem

OAuth client credentials can only be obtained **after** OpenEMR boots and
creates its database. But the agent needs those credentials in its `.env` to
authenticate. The `--fresh` deploy flag automates this:

1. Deploy starts all services with empty `OPENEMR_CLIENT_ID`/`OPENEMR_CLIENT_SECRET`
2. `register-oauth.sh` waits for MySQL and OpenEMR to be ready
3. Enables REST/FHIR APIs and `oauth_password_grant` in the `globals` table
4. Registers an OAuth2 client with `grant_types: ["password"]`
5. Enables the client in `oauth_clients` (disabled by default)
6. Injects credentials into `.env` on the server
7. Restarts the agent to pick up the new credentials
8. Deploy script pulls updated `.env` back to local `.env.prod`

### Things That Go Wrong (and have, repeatedly)

| Problem | Cause | Fix |
|---|---|---|
| `invalid_grant` / `Failed Authentication` | Wrong password — using `OPENEMR_ADMIN_PASS` instead of `pass` | Set `OPENEMR_PASS=pass` |
| `API is disabled` (404 on OAuth endpoints) | REST APIs not enabled in `globals` table | Run step 2 of `register-oauth.sh` |
| Client registered but token fails | `is_enabled=0` in `oauth_clients` | `UPDATE oauth_clients SET is_enabled=1 WHERE client_name='...'` |
| Client has wrong `grant_types` | Registered without `"grant_types": ["password"]` | Re-register with explicit grant_types |
| Deploy overwrites working `.env` | `deploy.sh` copies `.env.prod` → `.env` every time | Ensure `.env.prod` has current OAuth creds |
| `docker compose restart` doesn't pick up `.env` changes | `restart` reuses old env | Use `docker compose up -d` to recreate |
| OAuth secret lost | DB stores encrypted, plaintext only at registration | Re-register a new client |
| SSL certs gone after `--fresh` deploy | (FIXED) Certs were in a Docker named volume wiped by `down -v` | Now stored on host; `deploy.sh --fresh` re-obtains via certbot if missing |
| Sidebar not showing | Module not in `modules` table (seed SQL runs before table exists) | `register-oauth.sh` now handles this; or manually INSERT into `modules` |
| Sidebar iframe 404 (`/agent-api/ui`) | Apache reverse proxy not configured | `start.sh` adds `ProxyPass /agent-api/` at container startup; rebuild OpenEMR if missing |
| `Agent backend unreachable` in sidebar | `proxy.php` has hardcoded/stale Docker IP; `down -v`/`up` changes subnet | Use Docker service name `agent` or `OPENEMR_AGENT_API_URL` env var, NEVER hardcode IPs. Verify: `docker exec emr-agent-openemr-1 curl http://agent:8000/api/health` |
| `Unexpected token '<', "<script>"` in sidebar chat | (FIXED) `proxy.php` included `globals.php` without `$ignoreAuth=true`. On session timeout, `auth.inc.php` outputs a `<script>` redirect and calls `exit()`, never reaching proxy.php's own JSON 401 handler. | `proxy.php` now sets `$ignoreAuth = true` before `require_once globals.php` and does its own `$_SESSION['authUserID']` check |

### Manual OAuth Registration (if automation fails)

```bash
# 1. Enable APIs
ssh root@77.42.17.207 "docker exec emr-agent-mysql-1 mysql -uopenemr -p'<MYSQL_PW>' openemr -e \"
  UPDATE globals SET gl_value = 1 WHERE gl_name IN (
    'rest_api', 'rest_fhir_api', 'rest_portal_api',
    'rest_system_scopes_api', 'oauth_password_grant'
  );
\""

# 2. Register client (from inside OpenEMR container)
ssh root@77.42.17.207 "docker exec emr-agent-openemr-1 curl -s -X POST \
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
  }'"
# → Save the client_id and client_secret from the response!

# 3. Enable the client
ssh root@77.42.17.207 "docker exec emr-agent-mysql-1 mysql -uopenemr -p'<MYSQL_PW>' openemr -e \"
  UPDATE oauth_clients SET is_enabled = 1 WHERE client_name = 'Clinical Agent';
\""

# 4. Test token acquisition (password MUST be 'pass')
ssh root@77.42.17.207 "curl -s -X POST 'http://localhost:80/oauth2/default/token' \
  -d 'grant_type=password&username=admin&password=pass&client_id=<ID>&client_secret=<SECRET>&user_role=users&scope=openid%20api:oemr%20api:fhir%20user/Patient.read'"

# 5. Update .env and restart agent
ssh root@77.42.17.207 "sed -i 's|^OPENEMR_CLIENT_ID=.*|OPENEMR_CLIENT_ID=<ID>|; s|^OPENEMR_CLIENT_SECRET=.*|OPENEMR_CLIENT_SECRET=<SECRET>|' /opt/emr-agent/.env"
ssh root@77.42.17.207 "cd /opt/emr-agent && docker compose -f docker-compose.prod.yml --env-file .env up -d agent"

# 6. Pull .env back to local
scp root@77.42.17.207:/opt/emr-agent/.env .env.prod
```

## Running E2E Tests Against Prod

```bash
# Review panel tests (mocked, no LLM):
AGENT_BASE_URL=http://localhost:18000 \
  pytest tests/e2e/test_review_panel.py -m e2e -v

# Write loop tests (real LLM + DB):
AGENT_BASE_URL=http://localhost:18000 \
  E2E_SSH_HOST=root@77.42.17.207 \
  E2E_MYSQL_CONTAINER=emr-agent-mysql-1 \
  E2E_MYSQL_PASS=<mysql-openemr-pw> \
  pytest tests/e2e/test_write_loop.py -m e2e -v

# Eval suite:
AGENT_BASE_URL=http://localhost:18000 \
  pytest tests/e2e/test_agent_evals.py -m e2e -v
```

**Prerequisites:** SSH tunnel must be open: `ssh -L 18000:localhost:8000 root@77.42.17.207`

## Let's Encrypt / TLS

Certificates live on the **host** at `/etc/letsencrypt/` and are bind-mounted
read-only into the OpenEMR container. `docker compose down -v` does NOT
affect them — only a host-level `rm -rf /etc/letsencrypt` would.

- **Automatic provisioning:** `deploy.sh --fresh` checks for certs and runs
  `certbot certonly --standalone` if missing (port 80 must be free, which it is
  after the wipe).
- **Auto-renewal:** The `certbot.timer` systemd timer handles renewal. A deploy
  hook at `/etc/letsencrypt/renewal-hooks/deploy/restart-openemr.sh` restarts
  the OpenEMR container so `start.sh` copies the renewed certs into Apache.
- **Manual renewal:** `ssh root@<IP> "certbot renew --force-renewal"` — the
  deploy hook restarts OpenEMR automatically.

### How certs flow into Apache

1. Certbot stores certs at `/etc/letsencrypt/live/emragent.404.mn/`
2. `docker-compose.prod.yml` bind-mounts `/etc/letsencrypt:/etc/letsencrypt:ro`
3. `docker/start.sh` copies `fullchain.pem` → `/etc/ssl/certs/webserver.cert.pem`
   and `privkey.pem` → `/etc/ssl/private/webserver.key.pem` before Apache starts

### ⚠️ Previous bug: certs stored in Docker volume

Before this fix, certs were in a Docker named volume (`letsencrypt:`). Running
`docker compose down -v` destroyed them with no way to recover except
re-running certbot. Now they're on the host filesystem and immune to volume
wipes.

## First-Time Server Setup

```bash
# 1. Provision a fresh Ubuntu 24.04 VPS, then:
ssh root@<IP> 'bash -s' < scripts/server-setup.sh

# 2. Deploy (--fresh obtains Let's Encrypt certs automatically):
./scripts/deploy.sh <IP> --fresh
```

## Container Names

| Service | Container Name |
|---|---|
| MySQL | `emr-agent-mysql-1` |
| OpenEMR | `emr-agent-openemr-1` |
| Agent | `emr-agent-agent-1` |
| Jaeger | `emr-agent-jaeger-1` |

## Troubleshooting

**Agent logs:** `ssh root@77.42.17.207 "docker logs emr-agent-agent-1 --tail 50"`

**OpenEMR logs:** `ssh root@77.42.17.207 "docker logs emr-agent-openemr-1 --tail 50"`

**Check OAuth clients in DB:**
```bash
ssh root@77.42.17.207 "docker exec emr-agent-mysql-1 mysql -uopenemr -p'<PW>' openemr -e \
  'SELECT client_name, client_id, is_enabled, grant_types FROM oauth_clients'"
```

**Check API globals:**
```bash
ssh root@77.42.17.207 "docker exec emr-agent-mysql-1 mysql -uopenemr -p'<PW>' openemr -e \
  \"SELECT gl_name, gl_value FROM globals WHERE gl_name IN ('rest_api','rest_fhir_api','oauth_password_grant')\""
```

**Check sidebar module registration:**
```bash
ssh root@77.42.17.207 "docker exec emr-agent-mysql-1 mysql -uopenemr -p'<PW>' openemr -e \
  \"SELECT mod_name, mod_active, mod_ui_active FROM modules WHERE mod_name = 'ClinicalAssistant'\""
```

**Check agent-api proxy:**
```bash
ssh root@77.42.17.207 "curl -s -o /dev/null -w '%{http_code}' http://localhost/agent-api/ui"
# Should return 200. If 404, the ProxyPass rule is missing — rebuild OpenEMR container.
```
