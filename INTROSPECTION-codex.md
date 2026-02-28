# INTROSPECTION

## Scope And Method

- Reviewed entire local git history from first commit to `HEAD` (`43` commits, `2026-02-23` to `2026-02-27`).
- Extracted `Amp-Thread-ID` trailers from commit metadata and correlated each thread via Amp thread reads.
- Mapped repeated failures by recurrence across commits + thread narratives, then derived systemic root causes and corrective operating rules.

## Commit/Thread Correlation Summary

- Total commits: `43`
- Commits with explicit Amp thread trailer: `36`
- Commits missing explicit thread trailer: `7` (`6` feature/fix commits on `2026-02-25` + `1` merge commit)
- Distinct referenced Amp threads: `12`
- Largest single-thread batch: `T-019c9305-5346-70b5-be60-2da95e5fec01` (`16` commits)

## Repeated Issues And Process Failures

## 1) Architecture Decisions Based On Unverified Capability Assumptions

This was the dominant repeated failure: major implementation decisions were made before validating OpenEMR behavior against live endpoints and DB reality.

Evidence:
- `f779169c`: remove FHIR write path after discovering FHIR write support is mostly unavailable.
- `89f7cff8`: correct medication table model after prior misinformation.
- `7cbcb116`: align eval expectations with actual API capabilities.
- `c2863eee`: add SoapNote/Vital writes after earlier read-only assumptions were disproven.
- Thread signals: `T-019c90c8`, `T-019c95e1`, `T-019c9601`, `T-019c9827` repeatedly describe incorrect capability beliefs and later reversals.

Impact:
- Rework churn in core architecture and prompts.
- Eval baselines invalidated by shifting capability model.
- Loss of schedule due to redo cycles.

## 2) Prompt / Backend / Eval Contract Drift

System prompt rules, backend write capabilities, and eval expectations repeatedly diverged.

Evidence:
- `6680c5ec`: prompt overhaul introduces new constraints.
- `7cbcb116`, `f21938f5`: eval expectations had to be relaxed/realigned.
- `c2863eee`: backend adds new writable types, requiring prompt + eval updates.
- Thread signals: repeated mention of baseline mismatch and post-change regressions (`T-019c95e1`, `T-019c9601`, `T-019c9827`).

Impact:
- “Failing” evals that were actually contract mismatches.
- Frequent test reclassification instead of stable signal.

## 3) Write-Path Correctness Gaps Found After Feature Development

Critical write safety bugs were discovered after substantial implementation, not prevented by up-front invariant checks.

Evidence:
- `3bb8a909`: multiple write-path bugs fixed, including dangerous delete behavior.
- `761f789e`: add pre-caching workaround for null UUID/list-ID behavior.
- `4096f1a8`: verification path repaired after API changes.
- Thread signals: silent field drops and API accept-but-ignore behavior (`T-019c9305`).

Impact:
- Risk of false “success” states.
- Patient safety and data integrity risk if not caught.

## 4) Deployment And Bootstrap Fragility (OAuth/OpenEMR/Certs)

Bring-up/deploy repeatedly broke around initialization ordering and environment propagation.

Evidence:
- `bf0c1dc0`, `bada2689`, `8cdbe5ce`, `5e482748`, `0699e0c7`: repeated deployment workflow surgery.
- Thread signals: OAuth chicken-and-egg, OpenEMR long boot windows, env not reloaded on restart, cert persistence issues (`T-019c9328`, `T-019c9ad6`, `T-019c9b49`, `T-019c9f6a`).

Impact:
- Deployment cycle times of ~15 minutes before optimization.
- High operator error probability and repeated manual recovery.

## 5) Source-Of-Truth Hygiene Failures

Code existed in multiple effective locations with drift between git-tracked source and deployed/module copies.

Evidence:
- Thread `T-019c9f6f` explicitly identifies divergence between `web/sidebar/` and `openemr/.../assets/` copies and dead-code paths.
- Follow-up commits (`b6067d80`, `1a417f0a`, `635e2cd5`) are cleanup and behavior fixes after reconciliation.

Impact:
- Repository stopped representing production reality.
- Regressions introduced by patching wrong copy.

## 6) Oversized Change Batches And Late Integration

Repeated pattern of very large uncommitted or mixed-scope deltas spanning infrastructure, backend, UI, tests, docs.

Evidence:
- Thread `T-019c9305`: ~2700-line / 25-file uncommitted set requiring manual split.
- Thread `T-019c9ad6`: ~800 insertions across 15 files in one working batch.

Impact:
- Harder review and root-cause isolation.
- Increased probability of missed regressions and missing trailers.

## 7) Traceability Gaps In Commit Metadata

Not every commit includes the required Amp thread trailer, breaking forensic continuity.

Evidence:
- Missing trailer commits: `5f7f4251`, `8a32b0bc`, `348fc7fe`, `e06b76c5`, `f21938f5`, `73242280`, plus merge `6f43bf45`.
- These missing entries sit inside active thread work, making future audit correlation harder.

Impact:
- Lost decision provenance.
- Harder to reconstruct why a behavior exists.

## 8) Test Signal Instability And Non-Determinism

Eval outcomes were repeatedly influenced by timeouts, model lifecycle changes, and nondeterministic responses.

Evidence:
- `82550e80`: retries + timeout increases.
- `f21938f5`: LLM judge model migration and timeout tuning.
- Threads `T-019c95e1`, `T-019c9601`, `T-019c9827`: flaky cases, baseline drift, over-clarification variability.

Impact:
- Reduced confidence in pass/fail as release gate.
- Time spent tuning harness instead of stabilizing behavior.

## 9) Integration Bugs Escaping Until E2E/Runtime

User-visible regressions and UI interaction bugs were repeatedly discovered only after runtime integration.

Evidence:
- `bf0c1dc0`: send button disabled bug fixed.
- `635e2cd5`: overlay scroll/expand timing bug fixed.
- Threads mention standalone UI tests passing while embedded integration failed (`T-019c9328`).

Impact:
- Quality appears acceptable in isolated tests, but fails in real workflow.

## Why The Project Is Off-Track

The project is not behind because of insufficient effort; it is behind because feedback loops were structurally weak:

- Capability facts were learned after implementation, not before it.
- Contract synchronization (prompt/backend/eval) was manual and drift-prone.
- Deploy/bootstrap process consumed significant engineering time with repeated infra breakage.
- Large WIP batches and source-of-truth drift amplified regression risk.
- Commit-to-thread traceability was inconsistent, reducing diagnostic speed.

## Corrective Operating Model (Immediate)

## A) Capability Ledger Before Any Feature Work

Create and enforce `docs/CAPABILITY_LEDGER.md` as release-critical truth:
- For each resource/action: `read path`, `write path`, required IDs (`pid`/`uuid`/`eid`), known API caveats, and SQL confirmation query.
- No prompt/eval/backend change may merge unless ledger is updated in same PR.

## B) Contract-Lock Tests For Prompt/Backend/Eval Sync

Add one contract test suite that fails on drift:
- Parse prompt writable-type section and compare to backend translator/endpoint map.
- Validate eval expected write/refusal behavior against same map.
- Block merges on mismatch.

## C) Deployment As A Verified State Machine

Codify deploy flow into machine-checkable phases:
- `infra up` -> `openemr healthy` -> `oauth registered` -> `agent auth check` -> `sidebar route check`.
- `scripts/verify-deployment.sh` must gate eval runs and manual QA.
- Fail fast on first broken precondition; no partial "looks okay" deploys.

## D) Single Source Of Truth Enforcement

- Keep `web/sidebar/` as canonical.
- CI check: fail if tracked file hash differs from deployed asset path source at build time.
- Disallow direct edits under `openemr/.../assets/` except generated/symlinked artifacts.

## E) WIP And Commit Hygiene Rules

- Max logical scope per commit: one subsystem or one failure class.
- Require `Amp-Thread-ID` trailer on every non-merge commit.
- Require merge checklist: tests run, deploy verification run, capability ledger updated (if relevant).

## F) Stabilize Test Signal

- Pin judge and runtime models in one config file with explicit EOL date comments.
- Split eval dashboard into `deterministic checks` vs `LLM-behavior checks`.
- Track flake rate per case; cases above threshold are quarantined until redesigned.

## G) Daily Recovery Cadence Until Deadline

- 15-minute daily risk review: top 3 blockers + owner + next proof.
- End-of-day checkpoint requires: green contract-lock tests, deploy verification result, and updated risk burndown.

## Near-Term Success Criteria

The recovery plan is working only if these are true for 3 consecutive days:

- No commit without thread trailer (excluding merges).
- No prompt/backend/eval drift incidents.
- One-command deploy verification passes before every eval run.
- Zero edits to non-canonical sidebar asset copies.
- Eval run produces stable delta (no unexplained >2-case swing run-to-run).
