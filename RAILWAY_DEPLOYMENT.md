# Railway Deployment — De-mothballing Guide

The Railway deployment is **dormant** (all services scaled to zero, deployments removed).
The project, service configs, environment variables, and MySQL data are all preserved.

## Project Details

| Item | Value |
|------|-------|
| Project name | `emr-agent` |
| Project ID | `bfd4baa6-7339-456c-831f-977dc8a6a274` |
| Railway CLI | `~/.local/bin/railway` |
| Auth | `RAILWAY_API_TOKEN` from `RAILWAY_API_KEY` in `.env` |

### Services

| Service | Internal domain | Public domain (when active) |
|---------|-----------------|---------------------------|
| agent | `agent.railway.internal:8000` | `agent-production-c763.up.railway.app` |
| openemr | `openemr.railway.internal:80` | `openemr-production-f10c.up.railway.app` |
| jaeger | `jaeger.railway.internal:4317` (collector), `:16686` (UI) | auto-assigned |
| MySQL | Railway managed plugin | N/A |

---

## De-mothball: Bring Everything Back Up

### Prerequisites

```bash
# Auth setup (every shell session)
set -a && source .env && set +a
export RAILWAY_API_TOKEN="$RAILWAY_API_KEY"
RAILWAY=~/.local/bin/railway
PROJECT_ID=bfd4baa6-7339-456c-831f-977dc8a6a274
```

### Step 1: Deploy the agent service

```bash
$RAILWAY link --project $PROJECT_ID --service agent --environment production
$RAILWAY up --detach
```

Wait for build + healthcheck (~2 min). Verify:

```bash
# Internal health (from another Railway service) or add a temporary public domain:
$RAILWAY domain   # generates a public URL
curl https://agent-production-c763.up.railway.app/api/health
# Expected: {"status":"healthy","openemr_connected":true,"openemr_status":"ok"}
```

### Step 2: Redeploy OpenEMR

OpenEMR uses the `openemr/openemr:flex` image with a custom fork. It doesn't need
`railway up` — it's an image-based service that just needs to be restarted.

```bash
$RAILWAY link --project $PROJECT_ID --service openemr --environment production
# Trigger a redeploy from the Railway dashboard, or:
$RAILWAY up --detach
```

OpenEMR takes **~5 minutes** on cold start to clone the repo and initialize the DB.

### Step 3: Redeploy Jaeger

```bash
$RAILWAY link --project $PROJECT_ID --service jaeger --environment production
$RAILWAY up --detach
```

### Step 4: Verify the full stack

```bash
# Agent health
curl https://agent-production-c763.up.railway.app/api/health

# OpenEMR login page
curl -s -o /dev/null -w "%{http_code}" https://openemr-production-f10c.up.railway.app
# Expected: 302 (redirect to login)

# FHIR metadata through agent
curl https://agent-production-c763.up.railway.app/api/fhir/metadata | python3 -m json.tool | head -5
```

### Step 5: Remove public domain from agent (security)

The agent API should only be accessible via Railway's private network in production.
Only OpenEMR needs a public domain.

```bash
# Remove from Railway dashboard: Settings → Networking → remove the public domain
# The CLI doesn't support domain deletion — use the dashboard.
```

---

## Known Issues / Troubleshooting (2026-02-28)

### Public domain HTTPS hangs after TLS (V2 runtime)

**Symptom:** Container runs fine (liveness probes from `100.64.0.x` return 200 OK, `railway ssh` works,
`railway logs` show uvicorn running on port 8000), but `curl https://<domain>/api/health` times out.
HTTP port 80 returns a 301 from Fastly CDN, but HTTPS never gets a backend response.

**Root cause:** Not fully diagnosed. Railway Runtime V2 (`RAILWAY_BETA_ENABLE_RUNTIME_V2=1`, system-injected)
appears to have different public-domain routing behavior. Internal routing works; external edge routing does not.

**Things that did NOT fix it:**
- Changing `PORT` (tried 8000, 8080, Railway-injected)
- Removing/adding `EXPOSE 8000` in Dockerfile
- Clearing `healthcheckPath` on the service instance
- Cycling the public domain (delete + recreate via GraphQL API)
- Multiple redeployments

**Next steps if you hit this again:**
1. Check Railway status page and community Discord for V2 routing issues
2. Try disabling V2 runtime if Railway exposes a way to do so (it's system-injected)
3. Contact Railway support with deployment ID and "edge not routing to container" description

**What DOES work on Railway:** Build, deploy, SSH, internal networking, liveness probes.
The code itself is fine — this is a Railway platform routing issue.

---

## Mothball Again (Scale to Zero)

```bash
# Remove all deployments for each service
for svc in agent openemr jaeger; do
  $RAILWAY link --project $PROJECT_ID --service $svc --environment production
  $RAILWAY down -y
done
```

Verify everything is down:

```bash
curl -s -o /dev/null -w "%{http_code}" --max-time 10 https://agent-production-c763.up.railway.app/api/health
# Expected: 404 (no backend)
```

---

## Environment Variables (Agent Service)

All are preserved in Railway. Key ones:

| Variable | Value |
|----------|-------|
| `ANTHROPIC_API_KEY` | Set from `.env` |
| `OPENEMR_BASE_URL` | `http://openemr.railway.internal` |
| `OPENEMR_FHIR_URL` | `http://openemr.railway.internal/apis/default/fhir` |
| `OPENEMR_CLIENT_ID` | OAuth2 client registered in OpenEMR |
| `OPENEMR_CLIENT_SECRET` | OAuth2 secret registered in OpenEMR |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://jaeger.railway.internal:4317` |
| `SESSION_DB_PATH` | `data/sessions.db` |
| `PORT` | `8000` |

## Environment Variables (OpenEMR Service)

| Variable | Value |
|----------|-------|
| `OE_USER` | `admin` |
| `OE_PASS` | `pass` |
| `MYSQL_HOST` | `${{MySQL.MYSQLHOST}}` (Railway reference) |
| `MYSQL_ROOT_PASS` | `${{MySQL.MYSQLPASSWORD}}` |
| `FLEX_REPOSITORY` | `https://github.com/stephenchilcote-gauntlet/openemr.git` |
| `OPENEMR_AGENT_API_URL` | `http://agent.railway.internal:8000` |

---

## Test Data

Seed patients (already in MySQL, persisted across mothball/demothball):

| Patient | PID | Conditions |
|---------|-----|------------|
| Maria Santos | 4 | T2DM, Hypertension |
| James Kowalski | 5 | COPD, AFib, T2DM |
| Aisha Patel | 6 | Depression, Hypothyroidism |

If you need to re-seed: `./scripts/railway-seed.sh`

---

## Running Evals Against Railway

The eval runner at `tests/eval/runner.py` makes direct HTTP calls (no browser needed):

```bash
# Requires agent to have a public domain temporarily
source .venv/bin/activate
python -m tests.eval.run_eval --url https://agent-production-c763.up.railway.app --output data/eval_results.json
```

No LLM-as-judge by default — the runner uses only keyword/structural assertions.
Each case takes 15-60s (LLM round-trips through the agent). Full suite: ~30 min.

**Note:** Claude API 529 "Overloaded" errors are common. The agent client uses
`max_retries=5` with exponential backoff. If the API is persistently overloaded,
wait and retry later.

---

## Scripts Reference

| Script | Purpose |
|--------|---------|
| `scripts/deploy-railway.sh` | Full initial deployment (creates project, services, sets all vars) |
| `scripts/railway-seed.sh` | Seeds test patient data into MySQL |
| `scripts/seed_railway.py` | Python helper for FHIR-based seeding |

## Fresh Deploy (Nuclear Option)

If the project is deleted or corrupted, run the full deploy script:

```bash
./scripts/deploy-railway.sh
```

Then follow post-deploy steps in that script's output (seed data, register OAuth2 client).
