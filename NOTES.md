# Operational Notes — OpenEMR Clinical Agent

## Quick Start (Cold Boot)

```bash
# 1. Start Docker if not running
sudo systemctl start docker

# 2. Bring up all services
cd /home/login/PycharmProjects/gauntlet/week2_emr_agent
docker compose up -d

# 3. Wait for OpenEMR (flex image clones repo + runs setup on first boot — takes ~3-4 min)
#    Watch for readiness:
curl -s http://localhost:80/apis/default/fhir/metadata | head -c 100

# 4. Verify everything
curl -s http://localhost:8000/api/health
# → {"status":"healthy","openemr_connected":true}
```

## Services & Ports

| Service   | Container                      | Port(s)          | Notes                                       |
|-----------|--------------------------------|------------------|---------------------------------------------|
| MySQL 8.0 | week2_emr_agent-mysql-1        | 3306             | Root pw: `root`, DB: `openemr`              |
| OpenEMR   | week2_emr_agent-openemr-1      | 80 (HTTP), 443   | flex image, DEMO_MODE=standard              |
| Agent API | week2_emr_agent-agent-1        | 8000             | FastAPI + Uvicorn                            |
| Jaeger    | week2_emr_agent-jaeger-1       | 16686 (UI), 4317 | OTLP collector                              |

## Authentication

### OpenEMR Admin
- **Username:** `admin` / **Password:** `pass`
- Web UI: http://localhost:80

### OpenEMR OAuth2 (API Access)
- **Grant type:** password (Resource Owner)
- **Client ID / Secret:** stored in `.env` (`OPENEMR_CLIENT_ID`, `OPENEMR_CLIENT_SECRET`)
- **Token endpoint:** `http://localhost:80/oauth2/default/token`
- OAuth2 client was registered via `/oauth2/default/registration` and manually enabled:
  ```sql
  UPDATE oauth_clients SET is_enabled=1 WHERE client_name='...';
  ```
- New OAuth2 registrations default to `is_enabled=0` — always enable manually after registering.

### Anthropic API
- **Key:** stored in `.env` as `ANTHROPIC_API_KEY`
- The agent container picks it up via `docker-compose.yml` environment section.
- **Model:** `claude-sonnet-4-20250514` (set in `src/agent/loop.py`)
- After changing the key, rebuild: `docker compose up -d --build agent`

## Data

| Entity       | Count  | Source                                    |
|--------------|--------|-------------------------------------------|
| Patients     | 56     | 3 demo (DEMO_MODE) + 50 Synthea + 3 seed |
| Conditions   | 1,973  | Synthea CCDAs + seed script               |
| Medications  | 341    | Synthea CCDAs                             |
| Allergies    | 38     | Synthea CCDAs                             |
| Encounters   | 3,129  | Synthea CCDAs                             |

### Named Test Patients (from `scripts/seed_fhir.py`)
- **Maria Santos** — Type 2 Diabetes, Hypertension
- **James Kowalski** — COPD w/ Acute Exacerbation, Atrial Fibrillation, T2DM w/ Hyperglycemia
- **Aisha Patel** — Major Depressive Disorder (recurrent, moderate), Hypothyroidism

### Re-seeding Data
```bash
# Synthea bulk import (inside OpenEMR container)
docker exec -it week2_emr_agent-openemr-1 bash
php -r 'require "/var/www/localhost/htdocs/openemr/interface/modules/zend_modules/module/Carecoordination/src/Carecoordination/Controller/devtoolsLibrary.source"; importRandomPatients(50, true);'

# Custom patients via FHIR/REST
source .venv/bin/activate
python scripts/seed_fhir.py
```

### FHIR API Quirks
- OpenEMR's FHIR `Condition` endpoint is **read-only** — create conditions via REST API: `POST /apis/default/api/patient/{uuid}/medical_problem`
- `_summary=count` returns `total: 0` (broken); use `_count=1` and read the `total` field from the Bundle instead.
- Patient UUIDs from FHIR differ from PIDs in the DB — FHIR uses the `uuid` column.

## OpenEMR API Setup

These globals must be enabled (already done via SQL):
```
rest_api = 1
rest_fhir_api = 1
rest_portal_api = 1
rest_system_scopes_api = 1
oauth_password_grant = 1
```

To verify: `docker exec week2_emr_agent-mysql-1 mysql -uroot -proot openemr -e "SELECT gl_name, gl_value FROM globals WHERE gl_name LIKE 'rest%' OR gl_name LIKE 'oauth%';"`

## OpenEMR Flex Image

- Uses `FLEX_REPOSITORY` / `FLEX_REPOSITORY_BRANCH` to clone OpenEMR source at boot
- Currently pointed at fork: `https://github.com/stephenchilcote-gauntlet/openemr.git`
- **Startup takes 3-4 minutes** — the container clones the repo, runs composer, and sets up the DB schema
- Data persists across restarts via named volumes (`mysql_data`, `openemr_sites`)
- Do NOT use `EMPTY=yes` — it creates a restart loop (container can't find `sqlconf.php`)

## Forked OpenEMR Repo

- Local clone: `./openemr/` (in `.gitignore`)
- Origin: `https://github.com/stephenchilcote-gauntlet/openemr.git`
- Upstream: official OpenEMR repo
- Required by assignment for open-source contribution

## Testing

```bash
source .venv/bin/activate

# Unit tests (88 passing, no external deps)
python -m pytest tests/ -x -q

# Integration tests (need running OpenEMR)
python -m pytest tests/ -x -q -m integration
```

## Rebuilding

```bash
# Agent only (fast — cached layers)
docker compose up -d --build agent

# Full rebuild (nuclear option — preserves data volumes)
docker compose down && docker compose up -d --build

# Full rebuild INCLUDING data wipe
docker compose down -v && docker compose up -d --build
# Then re-run seed scripts after OpenEMR finishes setup (~4 min)
```

## Observability

- Jaeger UI: http://localhost:16686
- Service name: `openemr-agent`
- OTLP endpoint (gRPC): `http://jaeger:4317` (internal) / `http://localhost:4317` (host)
- FastAPI auto-instrumented via `opentelemetry-instrumentation-fastapi`

## Anthropic Model IDs (as of Feb 2026)

| Model            | API ID                        | Alias              | Notes                                    |
|------------------|-------------------------------|--------------------|-----------------------------------------|
| Claude Opus 4.6  | `claude-opus-4-6`             | `claude-opus-4-6`  | Latest frontier model                   |
| Claude Sonnet 4.6| `claude-sonnet-4-6`           | `claude-sonnet-4-6`| Best speed/intelligence balance         |
| Claude Haiku 4.5 | `claude-haiku-4-5-20251001`   | `claude-haiku-4-5` | Fastest, cheapest ($1/$5 per MTok)      |

- `claude-3-5-haiku-20241022` reached EOL on Feb 19, 2026 — no longer works.
- `claude-haiku-4-20250514` never existed — that was a hallucinated model ID.
- The LLM judge in `tests/e2e/llm_judge.py` uses Haiku 4.5 for cost-effective eval.
- Kimi K2.5 via OpenRouter (`moonshotai/kimi-k2.5`) needs `max_tokens >= 2048` because
  its reasoning tokens consume the token budget (empty content with `finish_reason: length`
  at lower values).

## Encounter Page DOM Structure

### Iframe Hierarchy
```
top window (main.php)
  └─ iframe[name="enc"]  →  encounter_top.php?set_encounter=X
       └─ iframe[name="enc-forms"]  →  forms.php  (Summary tab)
       └─ iframe[name="enctabs-N"]  →  load_form.php / view_form.php (dynamic tabs)
```

### Key Selectors (forms.php)
- `#encounter_forms` — main container
- `#partable` — all forms list
- `.form-holder` with `id="{formdir}~{form_id}"` (e.g., `soap~42`)
- `.form-header` > `.form_header h5` — form name
- `.form-detail .collapse` (`#divid_N`) — expandable content
- `.form-edit-button.btn-edit` — edit buttons
- `#navbarEncounterTitle` — encounter title bar
- No `data-uuid` attributes (unlike patient summary)

### Encounter Navigation
URL: `encounter_top.php?set_encounter={id}` → `navigateTab(url, "enc")` → `activateTabByName("enc")`

### vs Patient Summary
- Patient summary uses Bootstrap cards (`#medical_problem_ps_expand` etc.) with `data-uuid` rows
- Encounter uses flat `.form-holder` list with sequential `#divid_N` collapses
- Encounter has inner TabsWrapper tab system; patient summary is direct content

### overlay.js: Encounter resources (Encounter, SoapNote, Observation, etc.) have `supportsRowTarget: false` — overlays not yet implemented for enc tab.

## Module Script Injection
- **Working mechanism:** `ob_start()` output buffer in `openemr.bootstrap.php` injects `<script>` before `</body>` on every page
- **Broken mechanism:** `StyleFilterEvent` in `Bootstrap.php` — raw `<script>` tag gets mangled into `<link href="...">` by `Header::createElement()`
- `embed.js` only runs in `window.top` (iframe guard prevents recursion)
- Bootstrap loaded via `ModulesApplication::loadCustomModule()` which `include`s `openemr.bootstrap.php`

## Known Issues

- OpenEMR flex startup is slow (~3-4 min) — agent will fail FHIR calls during this window
- `restart: unless-stopped` means a misconfigured OpenEMR will restart-loop forever — watch `docker logs -f week2_emr_agent-openemr-1`
- Pydantic V2 deprecation warnings in tests (class-based config, `utcnow()`) — cosmetic only

## Deployment Gotchas

- **Stale `openemr/` tree**: The `openemr/` dir contains a full OpenEMR source copy including the module assets. `Dockerfile.openemr` COPYs `openemr/` first, then overwrites with `web/sidebar/*.js`. But the flex startup script may clobber the overwrite. Always sync: `cp web/sidebar/*.js openemr/interface/modules/custom_modules/oe-module-clinical-assistant/public/assets/` before building.
- **Playwright `page.frame(name='pat')` doesn't work**: embed.js restructures the DOM (wraps body children in `#ca-content`), which confuses Playwright's frame detection. Use `page.frame_locator('iframe[name=pat]')` instead.
- **embed.js `mount()` can run multiple times** if the IIFE guard passes before the sidebar div is created (race between DOMContentLoaded listeners). The guard inside `mount()` prevents this.
