# AgentForge: Pre-Search Document

**OpenEMR In-Page Clinical Agent**

*February 2026 • Week 2 Sprint*

---

## Summary

The project adds a native AI agent to a forked OpenEMR instance. OpenEMR is an open-source EHR (electronic health record) system. The agent runs as a sidebar panel within the OpenEMR web interface. A clinician describes a task in plain language; the agent reads the relevant patient data, plans all required changes to the record, presents those changes for review on the actual OpenEMR screens, and executes approved writes through the OpenEMR API.

The core design problem is human oversight without approval fatigue. Prompting the user before each individual write produces so many interruptions that users stop reading them — defeating the purpose. The solution is a two-phase workflow: the agent assembles a complete change plan before touching anything, then the clinician reviews each proposed change in context on the actual screen where it will land. One deliberate review replaces a stream of low-information prompts.

---

# Phase 1: Domain & Constraints

## 1. Domain

Healthcare. Host application: OpenEMR (open-source EHR, FHIR R4 API, active ONC certification). The agent is forked directly into the OpenEMR codebase, not built as an external integration.

Target tasks — representative day-to-day examples:

- Add diagnoses to the problem list with suggested ICD-10 codes for billing. *(ICD-10: standardized diagnosis classification codes required for insurance billing.)*
- Generate a discharge handoff summary and referral packet for a patient transferring to a post-acute facility.
- Draft and submit a prior authorization request for a procedure, drawing on clinical notes from the current encounter. *(Prior auth: insurer review required before certain procedures are covered.)*
- Schedule a follow-up appointment and generate an after-visit summary for the patient.
- Flag a chart for care management review and draft the referral note.
- Summarize a lab trend (e.g., three consecutive HbA1c results) and draft a plain-language patient message.

These tasks share a pattern: read data from multiple parts of the patient record, apply reasoning, write one or more outputs back. This is where AI assistance is highest-leverage and where manual work is most expensive in a clinical setting.

---

## 2. Scale & Performance

| **Dimension** | **Target** |
|---|---|
| **Response time** | Non-trivial tasks will take minutes end-to-end: the agent reads across multiple record sections, builds a manifest, and waits for human review before writing. The relevant latency targets are for feedback indicators, not completion: the first tool call should begin within 2 seconds of submission, and the UI should surface incremental progress (which records are being read, manifest items as they are added) throughout. A blank screen for 90 seconds is a UX failure even if the result is correct. |
| **Concurrency** | Demo: 1–5 users. Backend is stateless per session; scales horizontally. |
| **Cost per task** | ~$0.06 at 10K input + 2K output tokens (Claude Sonnet rates). At 20 tasks/user/day: ~$36/user/month. High for consumer software; within range for clinical productivity tooling. |

---

## 3. Reliability & Safety

- Every proposed change must cite the specific data source it was derived from. The agent cannot introduce clinical facts not present in retrieved records.
- No write to the patient record executes without explicit clinician approval. This is a structural guarantee — the write path is not callable without a preceding approval step.
- All writes go through the OpenEMR REST API, not direct database access. The application layer maintains HIPAA-required audit trails, handles field encryption, and enforces business rules that exist above the database.
- The system must be deployable in a HIPAA-compliant environment. PHI (protected health information) cannot transit any service without a signed BAA (Business Associate Agreement — a federally required contract when a vendor handles PHI on a covered entity's behalf).

---

## 4. Constraints

- One-week sprint with a hard Tuesday MVP gate. Architectural simplicity is a requirement.
- Builder has hands-on background in healthcare data integration — EHR API patterns, data pipeline work, the practical gaps between what APIs expose and what workflows need. This informs the tool design and the emphasis on FHIR coverage gaps.

---

# Phase 2: Architecture

## 5. Agent Workflow

The agent operates in two phases separated by a human review step.

**Phase A — Planning**

The agent receives the clinician's request and current page context (active patient, encounter, screen). It calls data-reading tools to gather what it needs — diagnoses, medications, notes, labs, encounter details — across as many tool calls as required. It then builds a change manifest: a structured list of every write it intends to make. Each manifest item specifies the target resource, the action (create / update / delete), the proposed value, the current value being replaced, and the source record that justifies the change. No writes execute during this phase.

**Approval Tour**

The UI navigates the clinician through each proposed change on the OpenEMR screen where it will land. A diagnosis addition appears on the problem list. A referral note appears in the notes interface. Each item requires an explicit acknowledgment. Manifest items carry dependency relationships — if a prerequisite item is rejected, dependent items are automatically flagged. Independent items can be approved or rejected individually.

**Phase B — Execution**

Approved writes fire sequentially through the OpenEMR API. Before each write, the agent re-reads the target field to confirm it has not changed since the manifest was built. Conflicts halt execution for the affected item and surface the discrepancy to the clinician.

---

## 6. Agent Implementation

Custom agentic loop, no third-party framework. The loop: receive request + page context → call read tools → build manifest → call `await_approval()` → execute approved writes. Approximately 300 lines of Python. Complexity in this project lives in the tool implementations, verification layer, and eval framework. A lightweight custom loop gives complete visibility into agent reasoning, which the observability and eval requirements depend on.

---

## 7. Tools

Five tools. All general-purpose — clinical reasoning is handled by the LLM, not encoded in tool signatures. New task types require new test cases, not new tools.

| **Tool** | **Purpose / Mechanism** |
|---|---|
| **`fhir_read(resource_type, params)`** | Reads clinical data via the OpenEMR FHIR R4 API. Covers patients, encounters, conditions, medication requests, observations, allergy intolerances, appointments, care plans, and documents. The FHIR CapabilityStatement endpoint (`/fhir/metadata`) returns a machine-readable manifest of supported resources and operations, which the agent queries to understand available data without hardcoded endpoint knowledge. |
| **`fhir_write(resource_type, payload)`** | Writes clinical data via the FHIR API. Only callable after manifest approval. Routing writes through the API (not direct DB) preserves application-layer audit trails, encrypted field handling, and business rule enforcement. |
| **`openemr_api(endpoint, method, payload)`** | Calls OpenEMR's custom REST API for operations outside FHIR scope — messaging, fax dispatch, scheduling workflows, administrative functions. Accompanied by injected documentation of available endpoints so the agent can reason about what operations are available. |
| **`get_page_context()`** | Reads active patient ID, encounter ID, page type, and visible form state from the current OpenEMR page via injected JavaScript. Gives the agent context about where the clinician is working without requiring them to specify it. |
| **`await_approval(manifest)`** | Pauses execution and hands the manifest to the approval tour UI. Returns with per-item approval status and any inline clinician modifications. First-class part of the agent loop. |

---

## 8. LLM & HIPAA

Model: Claude Sonnet (Anthropic). 200K context window handles full patient chart + schema documentation + conversation history in a single context. Tool use is reliable for multi-step calling loops.

HIPAA requires a signed BAA with any vendor that receives PHI. The production inference path uses Claude via AWS Bedrock, which is covered under the AWS BAA and isolates data within AWS infrastructure. The Anthropic API is used during development against synthetic data only — no PHI is ever present in the development environment. A single environment variable switches between inference targets; no code changes required.

---

## 9. Observability

OpenTelemetry (OTEL) instrumentation throughout the agent backend. Every LLM call, tool invocation, verification check, and manifest operation emits a span with attributes: token counts, latency, tool name, manifest item count, approval outcome.

The specific backend for collecting and querying traces is not locked in. Requirements: self-hostable (clinical deployments cannot route PHI-adjacent trace data through external SaaS), and capable of correlating eval results with production traces. OTEL's vendor neutrality means the collection backend can be swapped without instrumentation changes. Custom tooling for agent-specific eval analysis is likely as the eval framework matures.

---

## 10. Evaluation

The change manifest cleanly separates evaluation into two independent tracks.

**Planning Evaluation (Deterministic)**

Given a patient scenario and a clinician instruction, the agent should produce a specific manifest. Evaluation compares the produced manifest to the expected manifest structurally: resource type, action, proposed value, source citation. No LLM judge required. Runs in CI on every commit.

**Output Quality Evaluation (LLM-as-Judge)**

Free-text clinical documents (SOAP notes, patient messages, referral letters) are evaluated against a structured rubric: completeness, accuracy relative to source data, absence of unsupported claims, appropriate tone for intended audience. Scores stored alongside trace IDs.

**Test Suite — 50+ Cases**

| **Category** | **Description** |
|---|---|
| **Happy path planning (20)** | Common tasks with known correct manifests: add diagnosis, generate note, schedule follow-up, code encounter for billing. |
| **Edge cases (10)** | Missing record data, ambiguous instructions, conflicting values between record sections, partial encounters. |
| **Adversarial (10)** | Instructions that should trigger refusal or escalation: delete all diagnoses, mark patient deceased, modify records outside current patient scope, attempt to bypass approval. |
| **Output quality (10+)** | Free-text generation tasks scored by LLM-as-judge rubric. |

---

## 11. Verification Layer

Four checks run against the change manifest before the approval tour begins.

| **Check** | **Mechanism** |
|---|---|
| **Grounding checks** | Every manifest item must include a source field citing the FHIR resource it was derived from. The verification layer fetches that resource and confirms it exists and supports the claim. Hallucinated clinical facts fail this check before the clinician ever sees them. |
| **Constraint validation** | ICD-10 and CPT codes validated against authoritative code lists. Required document fields checked for presence. SOAP notes validated for all four sections. Deterministic; runs in milliseconds. |
| **Confidence gating** | Hedging language in agent reasoning ("possibly", "unclear", "might be") flags the corresponding manifest items. These items receive distinct visual treatment in the approval tour, signaling closer review is warranted. |
| **Conflict detection** | Immediately before each write, the target field is re-read from the API. If the value has changed since the manifest was built, the write halts and the conflict is presented to the clinician. |

---

# Phase 3: Refinement

## 12. Technology Stack

| **Decision** | **Resolution** |
|---|---|
| **Agent backend — initial** | Python / FastAPI. Used for architecture validation during the discovery phase. |
| **Agent backend — production** | Go. Compiled, strong static typing, widely used in health tech infrastructure. Planned strangler-fig rewrite at sprint end, using the eval suite as the acceptance test. Feasible because agent code is almost entirely HTTP calls, JSON parsing, and control flow — no complex algorithms that are difficult to translate. |
| **OpenEMR integration** | Thin PHP module (~200 lines) injected into the OpenEMR interface. Renders the sidebar panel, extracts page context via JavaScript, proxies requests to the backend. All agent logic lives in the sidecar service. |
| **LLM** | Claude Sonnet via Anthropic API (development) and AWS Bedrock (production / E2E testing). |
| **Inference routing** | Config-driven switch between Anthropic API (development) and Bedrock (production). No code changes required to switch targets. |
| **Observability** | OTEL instrumentation throughout. Self-hostable collection backend, specific tooling TBD. |
| **Data reads** | FHIR R4 API primary. Read-only MySQL connection as fallback for data not exposed via the API. |
| **Data writes** | OpenEMR REST API only. |
| **Test data (unit/CI)** | Local Docker OpenEMR, seeded with synthetic data. |
| **Test data (E2E)** | OpenEMR public demo instance. |
| **Deployment** | Railway. Docker Compose: OpenEMR and agent backend as separate services. |

---

## 13. Security

- **Prompt injection:** User instructions pass as user-turn content. Tool outputs are structured JSON. Neither is interpolated into the system prompt.
- **PHI handling:** Synthetic data in development. Production uses Bedrock; data stays within AWS infrastructure.
- **Credentials:** Environment variables only. No hardcoded keys.
- **Audit trail:** Writes go through the OpenEMR REST API, which generates HIPAA-required application-layer audit entries. OTEL traces provide an additional record of every tool call, keyed to user session.
- **Approval bypass:** The write tools are structurally gated behind `await_approval()`. No code path executes a write without a preceding approval.

---

## 14. Failure Modes

- **Tool failure:** Tools return typed error responses. The agent decides whether to retry, try an alternative approach, or surface an explanation to the clinician.
- **Ambiguous request:** The agent asks one clarifying question before proceeding. Ambiguity is logged in the trace.
- **FHIR coverage gap:** Falls back to read-only MySQL query. This path is logged separately to track which data types need expanded API coverage.
- **Tour abandonment:** Manifest is discarded if the session closes before the tour completes. No writes execute.
- **Record conflict:** Write halts for the affected item. Clinician chooses to replan, skip, or abort the manifest.

---

## 15. Open Source Contribution

Contribution type: Documentation — architecture post targeted at engineers working at the intersection of health IT and AI.

Deliverables:

- Forked OpenEMR repository with working implementation.
- Eval dataset (50+ cases, synthetic patient data, expected manifests, rubric scores). Documented limitations: synthetically generated data does not capture clinical edge cases that emerge from real workflows. Released as a scaffold, not ground truth.
- Architecture document covering the change manifest pattern, plan-then-confirm approval model, FHIR-first tool design, verification layer, and self-hosted OTEL observability for air-gapped clinical environments. This fills a gap — no comparable design documentation exists in the OpenEMR developer community.
- Post on the OpenEMR developer forums, framed as an invitation for critique from people with clinical deployment experience.

Scope is stated honestly: working research prototype. The gap between this and a clinically deployable system is real and mostly non-technical — clinical validation, regulatory pathway, liability, change management. Naming those gaps is what makes the contribution credible to this audience.

---

# Decisions Summary

| **Decision** | **Resolution** |
|---|---|
| **Domain** | Healthcare / OpenEMR |
| **Agent concept** | Native in-page clinical workflow assistant embedded in forked OpenEMR |
| **Task scope** | Arbitrary EMR workflow assistance via composable tools and change manifest |
| **Agent implementation** | Custom agentic loop in Python — no framework |
| **Backend (initial)** | Python / FastAPI |
| **Backend (production)** | Go — strangler-fig rewrite using eval suite as acceptance test |
| **OpenEMR integration** | Thin PHP sidebar module (~200 lines) proxying to sidecar backend |
| **LLM** | Claude Sonnet |
| **Dev inference** | Anthropic API (config-driven switch to Bedrock for E2E testing) |
| **Prod inference** | Claude via AWS Bedrock (under AWS HIPAA BAA) |
| **HIPAA strategy** | Synthetic data in dev; Bedrock in prod; PHI never sent to non-BAA services |
| **Tools (5)** | `fhir_read`, `fhir_write`, `openemr_api`, `get_page_context`, `await_approval` |
| **Read strategy** | FHIR R4 API primary; read-only MySQL fallback for API coverage gaps |
| **Write strategy** | OpenEMR REST API only |
| **Oversight model** | Plan-then-confirm: complete change manifest before any write; clinician tours affected screens with diff overlay before approving |
| **Verification (4 checks)** | Grounding, constraint validation, confidence gating, conflict detection |
| **Observability** | OTEL instrumentation throughout; self-hostable collection backend (TBD) |
| **Eval: planning** | Deterministic manifest comparison, runs in CI |
| **Eval: output quality** | LLM-as-judge with structured rubric |
| **Eval dataset** | 50+ cases: 20 happy path, 10 edge, 10 adversarial, 10+ quality |
| **Test data** | Local seeded OpenEMR (unit/CI) + public demo instance (E2E) |
| **Deployment** | Railway, Docker Compose |
| **Open source contribution** | Architecture post + forked repo + eval dataset with documented limitations |

---

# Appendix: Pre-Search Checklist Responses

*Each checklist item answered in sequence.*

---

## Phase 1: Define Your Constraints

### 1. Domain Selection

| **Question** | **Answer** |
|---|---|
| **Which domain?** | Healthcare. |
| **What specific use cases?** | Arbitrary clinical workflow assistance within an active OpenEMR encounter: documentation generation, diagnosis and billing code suggestion, care transition handoffs, prior authorization drafting, patient messaging, scheduling, care management referrals. |
| **Verification requirements?** | Every proposed record change must cite a retrieved source record. ICD-10 and CPT codes validated against authoritative code lists. Clinical documents validated for structural completeness. All writes require explicit clinician approval before execution. |
| **Data sources?** | OpenEMR FHIR R4 API (primary — patients, encounters, conditions, medications, observations, documents, appointments). OpenEMR custom REST API (messaging, scheduling, administrative workflows). Read-only MySQL connection (fallback for data not exposed via either API). |

---

### 2. Scale & Performance

| **Question** | **Answer** |
|---|---|
| **Expected query volume?** | Demo: low. Production story: clinic-scale, 10–50 concurrent users. |
| **Acceptable latency?** | Non-trivial tasks will take minutes end-to-end. Target: first tool call begins within 2 seconds of submission; UI surfaces incremental progress throughout. Completion time is not the latency target — visibility is. |
| **Concurrent user requirements?** | Demo: 1–5. Backend is stateless per session; scales horizontally. |
| **Cost constraints for LLM calls?** | ~$0.06/task at 10K input + 2K output tokens. ~$36/user/month at 20 tasks/day. Acceptable for clinical productivity tooling. |

---

### 3. Reliability Requirements

| **Question** | **Answer** |
|---|---|
| **Cost of a wrong answer?** | Direct: hallucinated diagnosis codes get billed to insurers; incorrect medications appear in handoff documents; wrong ICD-10 codes affect patient records. The verification layer and human approval step exist specifically to catch errors before they land. |
| **Non-negotiable verification?** | Grounding checks (every manifest item cites its source record). Constraint validation (code lists, required fields). Human approval before any write executes. |
| **Human-in-the-loop requirements?** | All writes require clinician approval via the change manifest tour. Approval is not a per-action prompt — the agent presents a complete plan, and the clinician reviews each change on the actual screen where it will land. |
| **Audit/compliance needs?** | All writes through the OpenEMR REST API to preserve HIPAA-required application-layer audit trails. Production inference via AWS Bedrock under the AWS BAA. OTEL traces provide a secondary audit record of every agent action, keyed to user session. |

---

### 4. Team & Skill Constraints

| **Question** | **Answer** |
|---|---|
| **Familiarity with agent frameworks?** | Strong. Custom implementation chosen deliberately — the agent loop is simple enough that a framework would add opacity without benefit. |
| **Experience with chosen domain?** | Background in healthcare data integration and EHR API patterns. Limited clinical content expertise, which informed the domain choice: workflow automation rather than clinical decision support, where verification requirements would depend on deep medical knowledge. |
| **Comfort with eval/testing frameworks?** | Strong on deterministic testing. LLM-as-judge scoring is newer territory — mitigated by using a structured rubric and treating judge outputs as data, not ground truth. |

---

## Phase 2: Architecture Discovery

### 5. Agent Framework Selection

| **Question** | **Answer** |
|---|---|
| **Framework choice?** | Custom. The agent loop is ~300 lines: receive request, call read tools, build manifest, call `await_approval()`, execute approved writes. Third-party frameworks add abstraction that obscures agent reasoning — a liability when observability and debuggability are first-class requirements. |
| **Single or multi-agent?** | Single agent per session. No multi-agent coordination required for the task scope. |
| **State management requirements?** | Conversation history and accumulated tool results maintained in memory within a session. No cross-session persistence required for the agent itself (the EMR is the persistent state store). |
| **Tool integration complexity?** | Moderate. FHIR API is well-documented and self-describing. Custom REST API requires injected documentation. MySQL fallback is straightforward read-only access. |

---

### 6. LLM Selection

| **Question** | **Answer** |
|---|---|
| **Model choice?** | Claude Sonnet (Anthropic). Selected for reliable tool use in multi-step calling loops and a 200K context window that accommodates full patient chart + schema documentation + conversation history. |
| **Function calling support?** | Required. The agent loop depends on reliable structured tool calls across 4–6 sequential invocations per task. |
| **Context window needs?** | Large. A full patient chart with history, medications, diagnoses, recent notes, and labs can exceed 50K tokens. 200K window provides headroom. |
| **Cost per query acceptable?** | Yes. ~$0.06/task is within range for clinical productivity tooling. |

---

### 7. Tool Design

| **Question** | **Answer** |
|---|---|
| **What tools?** | `fhir_read`, `fhir_write`, `openemr_api`, `get_page_context`, `await_approval`. All general-purpose — clinical reasoning lives in the LLM, not in tool signatures. |
| **External API dependencies?** | OpenEMR FHIR R4 API, OpenEMR custom REST API. Both are local to the OpenEMR deployment — no external third-party APIs required for core functionality. |
| **Mock vs real data for development?** | Local Docker OpenEMR with seeded synthetic data for unit tests and CI. OpenEMR public demo instance for E2E integration tests. |
| **Error handling per tool?** | Each tool returns a typed error response. Agent decides per error type: retry, alternative approach, or surface explanation to clinician. No silent failures. |

---

### 8. Observability Strategy

| **Question** | **Answer** |
|---|---|
| **Tooling choice?** | Custom OTEL instrumentation with a self-hostable collection backend. Specific backend TBD — likely custom tooling as the eval framework matures. External SaaS observability is not viable for clinical deployments that cannot route PHI-adjacent trace data off-premises. |
| **What metrics matter most?** | Per-step latency (LLM calls, tool execution, verification checks). Token counts (input/output per call). Manifest item count and approval outcomes. Tool error rates by type. |
| **Real-time monitoring needs?** | Progress visibility during plan assembly is a UX requirement. Backend monitoring of error rates and latency is standard operational need. |
| **Cost tracking requirements?** | Token counts per request are emitted as span attributes. Aggregate cost can be derived from these without a separate tracking system. |

---

### 9. Eval Approach

| **Question** | **Answer** |
|---|---|
| **How to measure correctness?** | Two tracks. Planning eval: deterministic comparison of produced change manifest against expected manifest (resource type, action, value, source citation). Output quality eval: LLM-as-judge against a structured rubric for free-text clinical documents. |
| **Ground truth data sources?** | Hand-authored expected manifests for planning eval cases. Rubric-scored outputs for quality eval. Synthetic patient data throughout — limitations documented in the open source contribution. |
| **Automated vs human evaluation?** | Planning eval is fully automated, runs in CI. Output quality eval uses LLM-as-judge (automated) with the rubric designed to be auditable by human reviewers. |
| **CI integration?** | Planning eval suite runs on every commit. Output quality eval runs on a slower cycle (nightly or on demand) due to LLM call costs. |

---

### 10. Verification Design

| **Question** | **Answer** |
|---|---|
| **What claims must be verified?** | Every manifest item: the proposed value must be derivable from a retrieved source record. Code values (ICD-10, CPT) must exist in authoritative code lists. Documents must meet structural requirements. |
| **Fact-checking data sources?** | FHIR resources retrieved during the planning phase serve as ground truth. ICD-10 and CPT code lists embedded in the verification layer. |
| **Confidence thresholds?** | Hedging language in agent reasoning flags manifest items for heightened human attention. No numeric threshold — flagging is lexical, not probabilistic, to keep it auditable. Agent stops and requests guidance if it can't prove its actions are safe and well-grounded. |
| **Escalation triggers?** | Requests to delete records in bulk, change irreversible status fields, or operate outside the current patient context trigger refusal rather than manifest generation. |

---

## Phase 3: Post-Stack Refinement

### 11. Failure Mode Analysis

| **Question** | **Answer** |
|---|---|
| **When tools fail?** | Typed error responses. Agent retries, tries an alternative approach, or surfaces an explanation. No silent failures. FHIR gaps fall back to MySQL read. |
| **Ambiguous queries?** | Agent reasons about safety of "healthy default" options, then asks clarifying questions until all ambiguity resolved. |
| **Rate limiting and fallback?** | Anthropic API: exponential backoff. Bedrock: AWS SDK retry logic. No cross-provider fallback in scope for this sprint. |
| **Graceful degradation?** | If plan assembly fails partway through, partial manifest is discarded. User is informed what was attempted and why it failed. No partial writes. |

---

### 12. Security Considerations

| **Question** | **Answer** |
|---|---|
| **Prompt injection prevention?** | User instructions passed as user-turn content only. Tool outputs are structured JSON. Neither is interpolated into the system prompt. |
| **Data leakage risks?** | Development uses synthetic data. Production inference via Bedrock; data stays within AWS. OTEL traces are self-hosted. |
| **API key management?** | Environment variables. No hardcoded credentials. |
| **Audit logging requirements?** | All writes through the OpenEMR REST API (application-layer HIPAA audit trail). OTEL spans provide secondary record of every tool call keyed to user session. |

---

### 13. Testing Strategy

| **Question** | **Answer** |
|---|---|
| **Unit tests for tools?** | Each tool tested against local Docker OpenEMR with seeded synthetic data. Typed error paths tested explicitly. |
| **Integration tests for agent flows?** | End-to-end flows tested against OpenEMR public demo instance. Planning eval suite covers the majority of integration scenarios. |
| **Adversarial testing approach?** | 10 adversarial cases in the eval suite covering bulk deletion, irreversible status changes, out-of-scope record access, and approval bypass attempts. |
| **Regression testing?** | Planning eval runs in CI on every commit. Output quality eval baseline established at MVP; regressions surfaced by score delta against baseline. |

---

### 14. Open Source Planning

| **Question** | **Answer** |
|---|---|
| **What will be released?** | Forked OpenEMR repository with working implementation. Eval dataset (50+ cases, synthetic data, expected manifests, rubric scores). Technical writeup explaining advantages and drawbacks of integrating an agent into an EMR and what it would look like in practice. |
| **Licensing?** | OpenEMR is GPL v3. The fork inherits GPL v3. Architecture document released under CC BY. |
| **Documentation requirements?** | README covering setup, architecture overview, and known limitations. Inline code documentation. Separate architecture post for the developer community. |
| **Community engagement?** | Post on OpenEMR developer forums framing the work as a design proposal and inviting critique from people with clinical deployment experience. |

---

### 15. Deployment & Operations

| **Question** | **Answer** |
|---|---|
| **Hosting approach?** | Railway (fly.io as a backup plan). Docker Compose: OpenEMR and agent backend as separate services. Production story: same Docker Compose in a HIPAA-compliant VPC with Bedrock as the inference endpoint. |
| **CI/CD for agent updates?** | GitHub Actions. Planning eval suite as the gate on every PR. |
| **Monitoring and alerting?** | OTEL-based. Specific alerting thresholds TBD once baseline behavior is established. |
| **Rollback strategy?** | For the EMR data, we will be working with fictional data that can be restored from backup trivially. Because no write executes without human approval, rollback risk is limited. Manifest is logged before execution; a compensating manifest can reverse most changes. Code rollback via standard Git revert + redeploy. |

---

### 16. Iteration Planning

| **Question** | **Answer** |
|---|---|
| **User feedback collection?** | Approval tour includes a lightweight thumbs up/down on the completed manifest. Feedback keyed to trace ID for correlation with eval results. |
| **Eval-driven improvement cycle?** | Planning eval regressions trigger prompt review before merge. Output quality score trends reviewed weekly. Production trace failures added to the eval dataset as new cases. |
| **Feature prioritization?** | Determined by which task types appear most frequently in production traces and which have the lowest eval pass rates. Data-driven, not speculative. |
| **Long-term maintenance?** | Outside scope for this sprint. I don't believe I can write something that's solid enough to be *actually used* as a foundation for work going forward, so I intend to produce a technical writeup on what a proposed implementation *could* look like with the code and evals as worked examples. |

---

*Pre-Search completed February 2026. All decisions recorded prior to writing any implementation code.*
