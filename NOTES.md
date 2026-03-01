# Operational Notes — OpenEMR Clinical Agent

## ⛔ NO LOCAL DOCKER

There is NO local Docker deployment. All services run on the **prod VPS**
at `emragent.404.mn` (77.42.17.207). See `docs/DEPLOY.md` for deployment.

The `docker/` directory contains Dockerfiles and scripts used exclusively by
the **prod deployment pipeline** (`scripts/deploy.sh`). Do not use them locally.

## Quick Reference

```bash
# Deploy agent to prod (~1 min)
./scripts/deploy.sh 77.42.17.207

# Full deploy (incl. OpenEMR rebuild)
./scripts/deploy.sh 77.42.17.207 --all

# SSH tunnels for internal services
ssh -L 8000:localhost:8000 root@77.42.17.207   # Agent API
ssh -L 16686:localhost:16686 root@77.42.17.207  # Jaeger UI

# Verify deployment
./scripts/verify-deployment.sh 77.42.17.207
```

## Prod Services

| Service   | Container                | Ports (on VPS)   | Notes                                       |
|-----------|--------------------------|------------------|---------------------------------------------|
| MySQL 8.0 | emr-agent-mysql-1        | internal only    | DB: `openemr`                               |
| OpenEMR   | emr-agent-openemr-1      | 80, 443 (public) | flex image, DEMO_MODE=standard              |
| Agent API | emr-agent-agent-1        | 127.0.0.1:8000   | FastAPI + Uvicorn (SSH tunnel only)          |
| Jaeger    | emr-agent-jaeger-1       | 127.0.0.1:16686  | OTLP collector                              |

## Authentication

### OpenEMR Admin
- **Username:** `admin` / **Password:** `pass`
- Web UI: https://emragent.404.mn

### OpenEMR OAuth2 (API Access)
- **Grant type:** password (Resource Owner)
- **Client ID / Secret:** stored in `.env.prod` (`OPENEMR_CLIENT_ID`, `OPENEMR_CLIENT_SECRET`)
- OAuth2 client was registered via `scripts/register-oauth.sh` and manually enabled.
- New OAuth2 registrations default to `is_enabled=0` — always enable manually after registering.

### Anthropic API
- **Key:** stored in `.env` as `ANTHROPIC_API_KEY`
- **Model:** `claude-sonnet-4-20250514` (set in `src/agent/loop.py`)

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

## FHIR API Quirks
- OpenEMR's FHIR `Condition` endpoint is **read-only** — create conditions via REST API: `POST /apis/default/api/patient/{uuid}/medical_problem`
- `_summary=count` returns `total: 0` (broken); use `_count=1` and read the `total` field from the Bundle instead.
- Patient UUIDs from FHIR differ from PIDs in the DB — FHIR uses the `uuid` column.

## OpenEMR API Setup

These globals must be enabled (already done on prod):
```
rest_api = 1
rest_fhir_api = 1
rest_portal_api = 1
rest_system_scopes_api = 1
oauth_password_grant = 1
```

## OpenEMR Flex Image

- Uses `FLEX_REPOSITORY` / `FLEX_REPOSITORY_BRANCH` to clone OpenEMR source at boot
- Currently pointed at fork: `https://github.com/stephenchilcote-gauntlet/openemr.git`
- **Startup takes 3-4 minutes** — the container clones the repo, runs composer, and sets up the DB schema
- Data persists across restarts via named volumes (`mysql_data`, `openemr_sites`)
- Do NOT use `EMPTY=yes` — it creates a restart loop (container can't find `sqlconf.php`)

## Forked OpenEMR Repo

- Origin: `https://github.com/stephenchilcote-gauntlet/openemr.git`
- Upstream: official OpenEMR repo
- Required by assignment for open-source contribution

## Testing

```bash
# Unit tests (no external deps)
uv run pytest tests/ -x -q

# E2E tests against prod (SSH tunnel required)
# See docs/DEPLOY.md "Running E2E Tests Against Prod"
```

## Observability

- Jaeger UI: `ssh -L 16686:localhost:16686 root@77.42.17.207` → http://localhost:16686
- Service name: `openemr-agent`
- OTLP endpoint (gRPC): `http://jaeger:4317` (internal container network)

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
- Pydantic V2 deprecation warnings in tests (class-based config, `utcnow()`) — cosmetic only

## Deployment Gotchas

- **Playwright `page.frame(name='pat')` doesn't work**: embed.js restructures the DOM (wraps body children in `#ca-content`), which confuses Playwright's frame detection. Use `page.frame_locator('iframe[name=pat]')` instead.
- **embed.js `mount()` can run multiple times** if the IIFE guard passes before the sidebar div is created (race between DOMContentLoaded listeners). The guard inside `mount()` prevents this.
