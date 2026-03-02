# AI Cost Analysis — OpenEMR Clinical Assistant Agent

## Development Costs

### Estimated Dev Spend (February 2026)

**Agent Development Phase**
- Baseline agent loop + tool scaffolding: ~2 hr × 3 API calls/min × 50 tokens avg = 180k tokens
- Claude Sonnet 4.6 @ $3/$15 per MTok: ~$1.20 (input) + $2.70 (output) = **~$3.90**

**Tool Integration & Testing**
- Tool validation runs (fhir_read, fhir_write, openemr_api, get_page_context): ~8 hours × 2 calls/min × 100 tokens avg = ~960k tokens
- Cost: ~$2.88 (input) + $14.40 (output) = **~$17.28**

**Verification Layer Development**
- Grounding, ICD-10 validation, conflict detection: ~4 hours × 1.5 calls/min × 200 tokens = ~720k tokens
- Cost: ~$2.16 (input) + $10.80 (output) = **~$12.96**

**E2E Test Suite & Evals (79 cases)**
- Single full eval run: 79 cases × 5 min each = 395 min (~6.5 hours)
- Per case: Playwright setup + agent query + LLM judge (Haiku) + assertions = ~2k tokens per case
- Total tokens: 79 × 2k = ~158k tokens
- Cost (Sonnet): $0.474 (input) + $2.37 (output) = **~$2.84** per run
- Estimated dev runs: ~8 full cycles (regression testing after fixes)
- Total: 8 × $2.84 = **~$22.72**

**LLM Judge Integration (Haiku 4.5 fallback)**
- Kimi K2.5 (OpenRouter) was primary; Haiku used as fallback for ~40 cases across dev iterations
- 40 cases × 500 tokens = 20k tokens
- Haiku @ $1/$5 per MTok: $0.02 (input) + $0.10 (output) = **~$0.12**

**Observability Setup & Tracing**
- OTEL/Jaeger integration: ~100 test runs × 1k tokens per trace log = ~100k tokens
- Cost: ~$0.30 (input) + $1.50 (output) = **~$1.80**

**Prompt Engineering & Refinement**
- System prompt iteration, tool descriptions, verification rules: ~10 refinement cycles
- 10 cycles × 100 tokens each = ~1k tokens
- Cost: ~$0.003 (input) + $0.015 (output) = **<$0.05**

### Total Development Spend
```
Agent Dev:           ~$3.90
Tool Integration:    ~$17.28
Verification:        ~$12.96
E2E Evals:          ~$22.72
LLM Judge:          ~$0.12
Observability:      ~$1.80
Prompt Engineering: ~$0.05
─────────────────────────────
TOTAL:             ~$58.83
```

**Note:** This excludes Anthropic's free tier testing and local model experimentation. Actual spend may be 15-20% higher due to failed queries, debugging iterations, and batch re-runs.

---

## Production Cost Projections

### Assumptions

| Variable | Value | Rationale |
|----------|-------|-----------|
| **Queries/user/day** | 5 | Clinicians query patient data 3-4 times/shift; doc generation ~2x/day |
| **Avg tokens/query (input)** | 3,500 | Patient context (conditions, meds, labs) + query + tool schemas |
| **Avg tokens/query (output)** | 1,200 | Synthesis + reasoning + manifest DSL (if generating writes) |
| **Tool calls/query** | 1.8 | Most queries: 1 fhir_read. Some multi-step (write scenarios) require 2-3 |
| **Manifest approval rate** | 40% | ~40% of queries result in a write manifest |
| **Model** | Claude Sonnet 4.6 | $3 input / $15 output per MTok |

### Per-Query Cost Calculation

**Single LLM Call (agent reasoning loop):**
- Input: 3,500 tokens @ $3/MTok = $0.0105
- Output: 1,200 tokens @ $15/MTok = $0.0180
- **Per-query baseline: ~$0.0285**

**Tool Execution Overhead:**
- fhir_read adds ~500 input tokens (schema) per call
- Tool result processing adds ~800 output tokens (parsing + formatting)
- Multi-tool queries (40% of volume) trigger 2-3 calls
- Average tool overhead: 0.4 × (500 input + 800 output tokens) = ~520 equiv tokens
- Additional cost: ~$0.0088
- **With tools: ~$0.0373 per query**

**Verification Layer:**
- Grounding checks, ICD-10 validation, conflict detection run locally (no LLM cost)
- Negligible token cost

**Manifest Write Flows (40% of queries):**
- Manifest generation + clinician approval → no additional LLM cost until approval
- Post-approval execution (fhir_write) triggers verification, not LLM calls
- **No added cost per manifest item**

### Scaling Scenarios

#### 100 Clinicians (Small Clinic)

| Metric | Value |
|--------|-------|
| **Queries/day** | 100 users × 5 queries = 500 |
| **Cost/query** | $0.0373 |
| **Daily cost** | 500 × $0.0373 = **$18.65** |
| **Monthly (30 days)** | **$559.50** |

#### 1,000 Clinicians (Regional Hospital)

| Metric | Value |
|--------|-------|
| **Queries/day** | 1,000 × 5 = 5,000 |
| **Daily cost** | 5,000 × $0.0373 = **$186.50** |
| **Monthly (30 days)** | **$5,595** |

#### 10,000 Clinicians (Health System / Multi-Hospital)

| Metric | Value |
|--------|-------|
| **Queries/day** | 10,000 × 5 = 50,000 |
| **Daily cost** | 50,000 × $0.0373 = **$1,865** |
| **Monthly (30 days)** | **$55,950** |

#### 100,000 Clinicians (National Scale)

| Metric | Value |
|--------|-------|
| **Queries/day** | 100,000 × 5 = 500,000 |
| **Daily cost** | 500,000 × $0.0373 = **$18,650** |
| **Monthly (30 days)** | **$559,500** |

---

## Cost Optimization Strategies

### 1. **Model Tier Selection**
   - **Current:** Claude Sonnet 4.6 ($3/$15 per MTok)
   - **Opportunity:** For lightweight queries (demographics, simple med lookups), switch to **Claude Haiku 4.5** ($1/$5 per MTok) — 67% cost reduction
   - **Estimate:** 30% of queries are "simple" → potential 20% overall savings (~$112k/month at 100k users)

### 2. **Prompt Caching** (via Anthropic API)
   - System prompt + FHIR schemas (constant context) = ~2,500 tokens
   - 5-minute cache window @ $0.30/MTok (cache writes) = $0.00075 per cached query
   - **Breakeven:** ~7 queries per cache period
   - Most clinicians exceed this → potential 10-15% cost reduction (~$56k–$84k/month at 100k users)

### 3. **Batch API for Offline Evals & Admin Tasks**
   - Batch API pricing: 50% discount ($1.50/$7.50 per MTok for Sonnet)
   - Non-interactive tasks (monthly audit reports, trend analysis) use Batch
   - **Estimate:** 5-10% volume → 2.5-5% overall savings (~$28k–$56k/month at 100k users)

### 4. **Tool Call Minimization**
   - Optimize fhir_read queries to reduce round trips (pre-fetch related resources)
   - Current avg 1.8 calls/query → target 1.4
   - ~22% reduction in tool execution overhead → ~$12k/month at 100k users

### 5. **Verification Layer Optimization**
   - Move more validation to local rule engine (e.g., drug interaction database) instead of LLM reasoning
   - Reduces reasoning tokens needed per query
   - **Estimate:** 5% token reduction → ~$28k/month at 100k users

### Combined Optimization Potential
- **Conservative estimate** (caching + model selection): ~20% savings
- **Aggressive estimate** (all strategies): ~35% savings

---

## Cost Comparison: Model Options

| Model | Input/Output | Use Case | Cost/Query |
|-------|-------------|----------|-----------|
| **Haiku 4.5** | $1/$5 | Simple queries, fast responses | ~$0.012 |
| **Sonnet 4.6** | $3/$15 | Current (reasoning, drug interactions) | ~$0.037 |
| **Opus 4.6** | $5/$25 | Complex clinical reasoning (overkill) | ~$0.062 |

**Recommendation:** Hybrid approach — Sonnet for complex reasoning, Haiku for simple lookups (10-15% cost reduction vs. all-Sonnet, no performance regression).

---

## Break-Even Analysis

### At What Scale Is the Agent Cost-Effective?

Assuming clinician time is worth **$100/hour** and agent adoption saves **2 minutes/query**:

- **Time savings:** 2 min × 100 clinicians × 5 queries/day × 30 days = 50k min/month = **833 hours/month = $83,300 in labor savings**
- **Agent cost @ 100 users:** ~$560/month (Sonnet 4.6)
- **ROI:** 83,300 / 560 = **148:1** return on investment

**Break-even:** The agent is cost-effective at even **1 clinician** (potential savings dwarf API costs). This holds across all scale tiers.

---

## Observability & Cost Tracking

**Current Implementation:**
- Token tracking via Anthropic API usage response (input/output per request)
- OTEL/Jaeger traces capture latency + token counts
- Manual monthly reconciliation against Anthropic API invoices

**Recommended Improvements (non-cost):**
- Integrate Finout or similar FinOps dashboard for real-time cost visibility
- Set up budget alerts in Anthropic Console (prevents runaway spend)
- Implement weekly cost reports by user/clinic for chargeback models

---

## Summary

| Metric | Value |
|--------|-------|
| **Dev spend (Feb 2026)** | ~$59 |
| **Monthly @ 100 users** | ~$560 |
| **Monthly @ 1K users** | ~$5,595 |
| **Monthly @ 10K users** | ~$55,950 |
| **Monthly @ 100K users** | ~$559,500 |
| **Cost/query (Sonnet 4.6)** | ~$0.037 |
| **Cost/query (Haiku 4.5)** | ~$0.012 |
| **ROI (labor savings)** | **148:1** (conservative) |
| **Optimization potential** | 20-35% cost reduction |

The agent achieves exceptional ROI even at small scale (single clinician) and benefits from significant optimization opportunities (prompt caching, model selection, batch processing) as volume grows.
