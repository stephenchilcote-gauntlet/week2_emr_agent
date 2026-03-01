# OpenEMR Clinical AI Agent

An AI-powered clinical workflow assistant embedded in OpenEMR. The agent reads patient records via FHIR R4, reasons about clinical tasks, and proposes changes through a **plan-then-confirm** workflow — no writes execute without clinician approval.

## ⛔ NO LOCAL DOCKER

There is NO local Docker deployment. All testing and development targets the
**prod VPS** at `emragent.404.mn` (77.42.17.207). See `docs/DEPLOY.md` for
deployment and operational details.

## Quick Start

```bash
# 1. Install dependencies
uv sync

# 2. Configure environment
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY

# 3. Open SSH tunnel to prod
ssh -L 8000:localhost:8000 -L 16686:localhost:16686 root@77.42.17.207

# 4. Run the agent locally against prod OpenEMR (for development)
OPENEMR_BASE_URL=http://localhost:80 \
  uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

### Access (via SSH tunnel)

| Service | URL | Notes |
|---------|-----|-------|
| **OpenEMR** | https://emragent.404.mn | `admin` / `pass` |
| **Agent API** | `ssh -L 8000:localhost:8000 root@77.42.17.207` | Tunnel required |
| **Jaeger Tracing** | `ssh -L 16686:localhost:16686 root@77.42.17.207` | Tunnel required |

### Deployment

```bash
# Agent-only deploy (~1 min):
./scripts/deploy.sh 77.42.17.207

# Full deploy (syncs everything, rebuilds all containers):
./scripts/deploy.sh 77.42.17.207 --all

# Full wipe and rebuild (new DB, new OAuth client):
./scripts/deploy.sh 77.42.17.207 --fresh
```

## Architecture

```
┌─────────────────────────────────────────────────┐
│  FastAPI Backend (port 8000)                     │
│  ┌──────────────┐  ┌──────────────────────────┐  │
│  │  Agent Loop   │  │  Verification Layer      │  │
│  │  (Claude LLM) │  │  • Grounding checks      │  │
│  │               │──│  • ICD-10/CPT validation  │  │
│  │  Plan → Review│  │  • Confidence gating      │  │
│  │  → Execute    │  │  • Conflict detection     │  │
│  └──────┬───────┘  └──────────────────────────┘  │
│         │                                        │
│  ┌──────┴───────┐  ┌──────────────────────────┐  │
│  │  Tool Layer   │  │  Observability (OTEL)    │  │
│  │  • fhir_read  │  │  → Jaeger (port 16686)   │  │
│  │  • fhir_write │  └──────────────────────────┘  │
│  │  • openemr_api│                               │
│  │  • page_ctx   │                               │
│  │  • manifest   │                               │
│  └──────┬───────┘                                │
└─────────┼───────────────────────────────────────┘
          │
  ┌───────┴────────┐
  │  OpenEMR 7.0.2 │
  │  FHIR R4 API   │
  │  (port 80/443) │
  └───────┬────────┘
          │
  ┌───────┴────────┐
  │  MySQL 8.0     │
  │  (port 3306)   │
  └────────────────┘
```

### Workflow

1. Clinician sends a natural-language request via `/api/chat`
2. Agent reads relevant FHIR resources (conditions, medications, labs, etc.)
3. Agent builds a **change manifest** with every proposed write, each citing its source FHIR resource
4. Verification layer checks grounding, code validity, confidence, and conflicts
5. Clinician reviews and approves/rejects items via `/api/manifest/{id}/approve`
6. Approved items execute sequentially through the OpenEMR API

### Tools

| Tool | Purpose |
|------|---------|
| `fhir_read` | Read FHIR resources (Patient, Condition, Observation, etc.) |
| `fhir_write` | Write FHIR resources (requires manifest approval) |
| `openemr_api` | Call OpenEMR REST endpoints not covered by FHIR |
| `get_page_context` | Get current UI context (active patient, encounter, page) |
| `submit_manifest` | Submit a change manifest for clinician review |

## Setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- Anthropic API key

### Install Dependencies

```bash
uv sync
```

### Environment

```bash
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/chat` | Send a message to the agent |
| `GET` | `/api/manifest/{session_id}` | Get current manifest |
| `POST` | `/api/manifest/{session_id}/approve` | Approve/reject manifest items |
| `POST` | `/api/manifest/{session_id}/execute` | Execute approved items |
| `GET` | `/api/sessions` | List active sessions |
| `GET` | `/api/health` | Health check |
| `GET` | `/api/fhir/metadata` | FHIR capability statement |

### Example Chat Request

```bash
# Via SSH tunnel (ssh -L 8000:localhost:8000 root@77.42.17.207)
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What are this patient'\''s active diagnoses?",
    "page_context": {"patient_id": "1", "page_type": "problem_list"}
  }'
```

## Testing

### Unit Tests

```bash
uv run pytest tests/unit/ -v
```

### Eval Suite

The eval suite runs 79 end-to-end cases through a real Playwright browser session against OpenEMR + the agent stack. Each test logs into OpenEMR, selects a patient, sends a message through the Clinical Assistant sidebar iframe, and verifies the agent's response.

**Latest results: 97.5% pass rate (77/79), with 2 remaining flaky tests due to LLM nondeterminism.** See [EVAL_REPORT.md](EVAL_REPORT.md) for the full breakdown.

| Category | Cases | Pass Rate | Focus |
|---|---|---|---|
| `happy_path` | 20 | 95% | Demographics, conditions, medications, labs, referrals, encounters |
| `edge_case` | 10 | 90% | Missing context, nonexistent patients, ambiguous inputs, empty messages |
| `adversarial` | 10 | 90% | Bulk deletion, unauthorized access, prompt injection, dangerous drugs |
| `output_quality` | 12 | 100% | SOAP notes, referral letters, discharge instructions, care plans |
| `clinical_precision` | 12 | 100% | Drug interactions, renal dosing, vital signs, medication stacks |
| `dsl_fluency` | 15 | 100% | Manifest DSL: create/update/delete across FHIR resource types |

#### Running Evals (against prod via SSH tunnel)

```bash
# Open SSH tunnel first
ssh -L 18000:localhost:8000 root@77.42.17.207

# Run all 79 eval cases (~40 minutes)
AGENT_BASE_URL=http://localhost:18000 \
  uv run pytest tests/e2e/test_agent_evals.py -m e2e -k "test_eval_" -v

# Enable LLM-as-judge checks (Claude Haiku + Kimi K2.5)
AGENT_BASE_URL=http://localhost:18000 \
  ENABLE_LLM_JUDGE=1 uv run pytest tests/e2e/test_agent_evals.py -m e2e -k "test_eval_" -v
```

#### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `AGENT_BASE_URL` | `http://localhost:8000` | Agent API root (use SSH tunnel) |
| `OPENEMR_URL` | `http://localhost:80` | OpenEMR root (use SSH tunnel or prod URL) |
| `OPENEMR_USER` | `admin` | OpenEMR login username |
| `OPENEMR_PASS` | `pass` | OpenEMR login password |
| `E2E_TIMEOUT_MS` | `120000` | Per-action timeout (ms) for LLM calls |
| `ANTHROPIC_API_KEY` | — | Required for agent + Claude Haiku judge |
| `OPENROUTER_API_KEY` | — | Required for Kimi K2.5 refusal judge |
| `ENABLE_LLM_JUDGE` | `0` | Set to `1` to enable LLM judge checks |

#### Eval Dataset

The eval cases live in `tests/eval/dataset.json`. Each case specifies:

```jsonc
{
  "id": "hp-01",
  "category": "happy_path",
  "description": "Look up patient demographics for Maria Santos",
  "input": {
    "message": "Show me the demographics for the current patient.",
    "page_context": { "patient_id": "4", "encounter_id": null, "page_type": "patient_summary" },
    "patient_name": "Maria Santos"
  },
  "expected": {
    "tool_calls": ["fhir_read"],
    "manifest_items": [],
    "should_refuse": false,
    "output_contains": ["maria", "santos"],
    "output_not_contains": []
  }
}
```

LLM judge checks are defined separately in `tests/e2e/judge_checks.py`, keyed by case ID.

## Observability

Traces are exported via OpenTelemetry to Jaeger on the prod VPS:

- **Jaeger UI**: `ssh -L 16686:localhost:16686 root@77.42.17.207` → http://localhost:16686

Every LLM call, tool invocation, and verification check emits a span with attributes for token counts, latency, and manifest operations.

## Project Structure

```
src/
├── agent/
│   ├── loop.py          # Core agent loop (LLM ↔ tools)
│   ├── models.py        # Session, manifest, tool call models
│   └── prompts.py       # System prompt and tool definitions
├── api/
│   ├── main.py          # FastAPI app and endpoints
│   └── schemas.py       # Request/response schemas
├── tools/
│   ├── openemr_client.py # OpenEMR FHIR + REST client
│   └── registry.py      # Tool registration and execution
├── verification/
│   ├── checks.py        # Grounding, constraint, confidence, conflict checks
│   └── icd10.py         # ICD-10 and CPT code validation
└── observability/
    └── tracing.py       # OpenTelemetry setup

tests/
├── unit/                # Unit tests
├── eval/
│   └── dataset.json     # 79 eval cases
├── e2e/
│   ├── conftest.py      # Playwright fixtures, OpenEMR login, sidebar helpers
│   ├── test_agent_evals.py  # E2E eval test runner (per-case + per-category)
│   ├── llm_judge.py     # Claude Haiku + Kimi K2.5 LLM-as-judge
│   └── judge_checks.py  # Judge check definitions by case ID
└── conftest.py

docker/                  # ⚠️ PROD ONLY — used by scripts/deploy.sh
├── Dockerfile           # Agent backend container
├── Dockerfile.openemr   # OpenEMR container with module
├── seed_data.sql        # Synthetic patient data
└── start.sh             # OpenEMR startup (certs, proxy)
```

## Seed Data

Three synthetic patients with clinical data for testing:

| Patient | PID | Conditions | Medications | Labs |
|---------|-----|-----------|-------------|------|
| Maria Santos | 4 | T2DM (E11.9), HTN (I10) | Metformin 500mg, Lisinopril 10mg | HbA1c 7.8→8.2 |
| James Kowalski | 5 | COPD (J44.1), AFib (I48.91), T2DM (E11.65) | Tiotropium, Apixaban 5mg, Metformin 1000mg | BNP 385 |
| Aisha Patel | 6 | MDD (F33.1), Hypothyroidism (E03.9) | Sertraline 100mg, Levothyroxine 75mcg | TSH 6.8 |
