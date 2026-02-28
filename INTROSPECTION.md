# Project Failure Introspection

## Method

Reviewed all 43 commits (2026-02-23 to 2026-02-28) and correlated against all 13
distinct Amp threads referenced in commit metadata. Seven commits lack thread
trailers entirely. Each thread was read to extract debugging struggles, rework
cycles, wasted effort, and decision failures.

---

## Timeline & Thread Map

| Date | Thread | Commits | Summary |
|------|--------|---------|---------|
| Feb 23 15:26 | T-…9d62 | 1 | Project spec, design research |
| Feb 23 15:50 | T-…01bd8 | 1 | Mega-build: agent, API, 5 tools, verification, Docker, 52 eval cases (36 files, 5376 lines) |
| Feb 24 12:01 | T-…793e | 1 | **Rip out FHIR write path** — 365 lines deleted, 28 tests rewritten |
| Feb 24 22:19–22:59 | T-…ec01 | **16** | Retroactive commit split of massive uncommitted batch (~2700 ins, 25 files) |
| Feb 24 23:35 | T-…c918 | 1 | Hetzner deployment (Fly.io abandoned), send-button bug |
| Feb 25 11:39 | T-…d493 | 3 | Medication table myth debunked, API hardening, prod deployment rework |
| Feb 25 12:23 | T-…7270 | 3 | Eval expectations wrong → SoapNote/Vital added, ServiceRequest removed |
| Feb 25 22:16–22:17 | T-…d042 + **no trailer** | 6 | Label system replaced, med safety, audit trail, tour mode, eval fixes |
| Feb 26 10:45 | T-…37de | 6 | Docker, deploy, UI, tests, docs — another big batch |
| Feb 26 12:50–13:13 | T-…70efc | 2 | Overlay engine + sidebar update |
| Feb 27 08:07–08:14 | T-…98a + T-…e873 | 2 | Deploy optimization (15 min → 1 min), overlay scroll fix |
| Feb 28 08:14 | T-…c0c2 | 1 | Previous introspection report |

**Key observation:** 5 of the 6 project days were spent primarily on rework,
deployment debugging, and fixing incorrect assumptions. Net-new user-visible
feature work was concentrated in just 2 sessions (the initial build and the
Feb 25 feature batch).

---

## Repeated Failure #1: Build First, Verify Never

**The single most damaging pattern.** Major subsystems were designed and fully
implemented based on assumptions about OpenEMR's API that turned out to be wrong.
The assumptions were never tested against the live system before coding began.

### Instance A: The FHIR Write Path (wasted: ~1 full day)

- **Thread T-…01bd8** (Feb 23): Built `fhir_write()`, 7 FHIR builder functions,
  `translator.py` (533 lines), and 28+ associated tests.
- **Thread T-…793e** (Feb 24): Discovered OpenEMR FHIR is **read-only** for nearly
  all clinical resources. Deleted 365 lines. Rewrote 28 tests. Redesigned write
  architecture around REST API.
- **Cost:** The entire write subsystem was thrown away. The translator was reduced
  from 533 → 168 lines (68% deletion).

### Instance B: The Medication "Two Tables" Myth (wasted: ~3 hours)

- **Thread T-…d493** (Feb 25): Code and prompts contained a claim that OpenEMR
  stores medications in separate `lists` + `prescriptions` tables. This was
  "disproven during debugging — the prescriptions table was empty, everything is
  in the lists table."
- The real bug was NULL UUIDs from REST POST, which was masked by the wrong
  mental model.
- **Cost:** Debugging pointed the wrong direction. Prompts had to be rewritten.
  The `_resolve_list_id` logic and error messages were wrong.

### Instance C: ServiceRequest Write Endpoints (wasted: ~2 hours)

- **Thread T-…7270** (Feb 25): Eval cases expected the agent to create
  `ServiceRequest` resources. Investigation proved "genuinely NO write endpoint"
  exists. Three eval cases (hp-13, dsl-02, dsl-10) had to be rewritten.
- **Cost:** Eval baseline invalidated. Time spent debugging "failures" that
  were actually impossible expectations.

### Instance D: DocumentReference/Observation Mapping (wasted: ~2 hours)

- Same thread: eval cases expected `DocumentReference` and `Observation` types.
  These had to be remapped to `SoapNote` and `Vital` once actual REST API
  routes were discovered by reading PHP source (`_rest_routes.inc.php`).
- The user noted: "had to perform a thorough ground-truth investigation by
  manually reading PHP route definitions."

### Root cause

No capability discovery step existed. The project assumed API documentation
was accurate and complete. It wasn't. A 30-minute session running `curl`
against every REST endpoint on day 1 would have prevented all four instances.

---

## Repeated Failure #2: Massive Uncommitted Work Sessions

The project repeatedly accumulated enormous uncommitted deltas, then had to
spend time retroactively decomposing them into commits.

### Instance A: The 16-Commit Dump (Thread T-…ec01, Feb 24)

- User asked to commit 4 bug fixes. `git status` revealed **25 files modified,
  2739 insertions, 509 deletions**, plus dozens of untracked files.
- The agent had to `git diff` every file to "understand" its own changes.
- Result: 16 retroactive commits covering DSL parser, web UI, session
  persistence, eval expansion, verification overhaul, PHI-safe tracing,
  and documentation — all in one session.
- **Cost:** Review impossible. Regression risk high. Several commits in this
  batch turned out to contain bugs fixed in later threads.

### Instance B: The 6-Commit Batch (Thread T-…37de, Feb 26)

- ~950 lines across 15 files, spanning Docker, backend, frontend, and tests.
- Again committed as a single batch after the fact.

### Instance C: Missing Thread Trailers (Feb 25, 5 commits)

- Five feature commits (medication safety, prompt guidance, audit trail, tour
  mode, eval fixes) lack `Amp-Thread-ID` trailers entirely.
- These changes cannot be correlated to any discussion context for forensic
  purposes.

### Root cause

No discipline around incremental commit cadence. Work sessions ran until
context was exhausted, then everything was dumped at once. The AGENTS.md
rule "Always commit after doing any work" was not followed.

---

## Repeated Failure #3: Prompt/Backend/Eval Contract Drift

The system has three independently evolving contracts that must agree:
1. **System prompt** — tells the LLM what resources are writable
2. **Backend** — translator/endpoint map that actually executes writes
3. **Eval dataset** — expected behaviors for test cases

These three diverged **at least 4 times**, each time producing a batch of
false failures.

| Date | What changed | What broke |
|------|-------------|------------|
| Feb 24 | Prompt restricted writable types (6680c5e) | 7 eval cases now expected impossible writes |
| Feb 25 | Backend added SoapNote/Vital (c2863ee) | Eval expected old types, prompt not updated |
| Feb 25 | Prompt added "don't re-confirm" guidance | Agent behavior changed, 3 evals failed |
| Feb 25 | LLM judge model hit EOL | All eval judgments invalid until model updated |

### Root cause

No automated check that these three contracts are in sync. Each was updated
manually. When one changed, the others were forgotten until failures appeared.

---

## Repeated Failure #4: Deployment as a Time Sink

At least **6 of 13 threads** (46%) dealt primarily with deployment problems.
The deployment surface area was enormous: OpenEMR + MySQL + Agent + Jaeger +
OAuth2 + SSL + Apache proxy + module registration.

### The Deployment Bug Hall of Fame

(Sourced from `DEPLOY.md`'s "Things That Go Wrong (and have, repeatedly)" table)

| Bug | Times hit | Thread(s) |
|-----|-----------|-----------|
| OAuth `invalid_grant` (wrong password var) | 3+ | T-…c918, T-…37de, T-…70efc |
| `is_enabled=0` in oauth_clients | 3+ | T-…c918, T-…d493 |
| REST APIs not enabled in globals | 2+ | T-…c918, T-…37de |
| Deploy overwrites working `.env` | 3+ | T-…37de, T-…70efc |
| Module not in `modules` table | 2+ | T-…c918, T-…37de |
| Apache proxy not configured | 2+ | T-…37de, T-…70efc |
| SSL certs lost on `docker down -v` | 1 | T-…37de |
| Sidebar 404 / stale Docker IP | 2+ | T-…70efc |

### Platform Pivot: Fly.io → Hetzner VPS

- Thread T-…c918 (Feb 24): Fly.io's ephemeral model was fundamentally
  incompatible with MySQL + persistent volumes + OpenEMR's heavy PHP stack.
  All Fly.io planning was wasted. Pivoted to raw VPS.

### Deployment Cycle Time

- Original deploy: **~15 minutes** (2.1GB openemr/ rsync + full rebuild)
- After optimization (Feb 27): **~1 minute** (agent-only mode)
- This optimization came on day 5 of 7. Days 2–4 ran at 15-min deploy cycles.

### Root cause

- OpenEMR was not designed to be deployed as a containerized microservice.
  The project fought this reality at every step.
- OAuth2 bootstrap has a chicken-and-egg problem (creds need DB, DB needs
  OpenEMR boot, agent needs creds) that was never fully automated.
- No deployment verification script existed until late in the project.

---

## Repeated Failure #5: Rework of Core Abstractions

Multiple core abstractions were built, found inadequate, and replaced entirely.

### The Label System (built twice)

1. **First version** (Feb 24, T-…ec01): `LabelRegistry` — 256-word XOR
   compression, stateful, collision-prone. Required session state management.
2. **Second version** (Feb 25, T-…d042): Bijective 10-word UUID encoding —
   stateless, zero collisions. Required removing `LabelRegistry` from
   `AgentSession` and all call sites.
- **Cost:** The entire first label system was thrown away 24 hours after creation.

### The Write Path (redesigned twice)

1. **FHIR writes** (Feb 23): Direct FHIR POST/PUT.
2. **REST API writes** (Feb 24): After FHIR proved read-only.
3. **Manifest DSL** (Feb 24): XML-based manifest approval flow.
- Each redesign required updating translator, executor, verification,
  prompts, and tests.

### Root cause

Prototyping was done at production scale. Instead of spiking a minimal
proof-of-concept to validate the approach, full implementations with tests
and documentation were built before the core design was validated.

---

## Repeated Failure #6: Subagent/Tooling Failures

### create_file Crashes (Thread T-…9d62, Feb 23)

- Subagents building the eval framework and dataset crashed repeatedly with
  the same error: `create_file: content parameter is required`.
- The main agent had to manually intervene to complete the work.
- This happened on the very first thread, during initial project setup.

### Model Lifecycle Chaos

- `claude-3-5-haiku-20241022` hit EOL on Feb 19, 2026. The project was still
  referencing it.
- `claude-haiku-4-20250514` was a **hallucinated model ID** that was baked
  into the codebase.
- Kimi K2.5 via OpenRouter needed `max_tokens >= 2048` due to reasoning token
  consumption — discovered only after repeated empty responses.

### Root cause

External dependencies (LLM model availability, tool behavior) were treated
as stable when they were not. No pinning, no EOL tracking, no validation.

---

## Repeated Failure #7: Test Signal Never Stabilized

The eval suite went through at least 4 major expectation rewrites and never
produced consistent signal for more than one session.

| Date | Eval event | Result |
|------|-----------|--------|
| Feb 23 | Initial 52 cases created | Baseline claimed 97.5% pass |
| Feb 24 | Expanded to 79 cases | Untested against live system |
| Feb 25 | 7 cases failed due to prompt restriction | Expectations rewritten |
| Feb 25 | 5 more cases failed (SoapNote/Vital mismatch) | Expectations rewritten again |
| Feb 25 | 3 cases failed (agent over-clarifying) | Attributed to model behavior, not fixed |
| Feb 25 | Judge model hit EOL | Judge migrated, timeout increased |
| Feb 25 | Known flaky tests (hp-02, ec-04, adv-06) | Acknowledged but not quarantined |

The eval suite was never a reliable release gate. Every run required manual
interpretation. "Pass rate" numbers were meaningless because the expected
answers kept changing.

### Root cause

Eval cases were written against assumed behavior, not observed behavior.
When reality differed, the tests were changed to match rather than the
system being fixed to match the tests. This inverts the purpose of testing.

---

## Repeated Failure #8: Source-of-Truth Divergence

Sidebar UI code existed in three locations:
1. `web/sidebar/` (git-tracked, canonical)
2. `openemr/interface/modules/custom_modules/.../assets/` (fork, deployed)
3. Docker bind-mount overlays

Thread T-…70efc explicitly identified divergence between these copies.
Subsequent commits (b6067d8, 1a417f0, 635e2cd) were cleanup after
reconciliation. The `embed.js` in the local repo used a different API path
(`/agent-api/ui`) than the fork version (`sidebar_frame.php`).

### Root cause

The project needed to inject code into a PHP application that has its own
module system. Rather than establishing a single build pipeline on day 1,
files were manually copied between locations and diverged.

---

## Where Did the Time Actually Go?

Estimated breakdown of engineering effort across the 6-day development period:

| Activity | Est. hours | % | Value |
|----------|-----------|---|-------|
| Deployment/infra debugging | ~12 | 25% | Low — mostly rework |
| Reworking incorrect assumptions (FHIR, meds, evals) | ~10 | 21% | Zero — pure waste |
| Retroactive commit organization | ~3 | 6% | Zero — process debt |
| Feature development (agent, tools, UI, safety) | ~14 | 29% | High |
| Eval framework & test writing | ~5 | 10% | Medium — unstable |
| Documentation | ~3 | 6% | Medium |
| Design/research | ~1 | 2% | High |

**~52% of total effort produced no lasting value.** The productive 48%
built a functional agent with a real UI, but the rework overhead prevented
the project from reaching production quality.

---

## Root Cause Summary

The project is off-track for one fundamental reason: **the feedback loop
between "what does the system actually do" and "what are we building" was
broken.** This manifested as:

1. **No capability discovery before implementation.** OpenEMR's actual API
   surface was never mapped. Every incorrect assumption became a rework cycle.

2. **No contract enforcement.** Prompt, backend, and eval were manually
   synchronized. They drifted constantly.

3. **No incremental validation.** Work accumulated in huge batches. Bugs
   were discovered late. Commits were retroactive.

4. **Infrastructure treated as solved.** Deployment was assumed to be a
   one-time cost. It was actually the project's largest ongoing time sink.

5. **Tests followed the code, not the other way around.** When tests failed,
   expectations were changed rather than behavior being fixed. The eval suite
   never served as a stability gate.

---

## What Must Change Now

### 1. Stop building things that haven't been verified to work

Before touching any code, run the actual API call. Confirm the endpoint
exists, accepts the payload, and returns what you expect. Document it in
a capability ledger. This takes 5 minutes and saves 5 hours.

### 2. Freeze the eval expectations

The eval suite has been rewritten 4 times. Pick the current state, run it
once, record the results, and never change expectations again. Report
actual pass rate honestly. A stable 75% is more credible than a
self-adjusting 97.5%.

### 3. One-command deploy verification before every change

`scripts/verify-deployment.sh` must pass before any eval run or demo
recording. No more partial deploys that "look okay."

### 4. Commit after every logical change, not at end of session

The AGENTS.md rule exists. Follow it. A 50-line commit with a clear
message is worth more than a 2700-line dump that requires archaeology.
