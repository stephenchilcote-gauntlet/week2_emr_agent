# §5 Research Notes — The Change Manifest

## 1. Key Source Files

| File | What it defines |
|------|----------------|
| `src/agent/models.py` L23–49 | `ManifestAction`, `ManifestItem`, `ChangeManifest` Pydantic models |
| `src/agent/loop.py` L155–251 | `_execute_tool` — gating fhir_write behind approval |
| `src/agent/loop.py` L253–312 | `execute_approved` — post-approval execution with topo sort |
| `src/agent/loop.py` L404–432 | `_build_manifest` — parsing LLM tool arguments into a manifest |
| `src/agent/loop.py` L434–455 | `_is_item_approved`, `_mark_item_executed` helpers |
| `src/agent/loop.py` L456–476 | `_topological_sort` — DFS topological sort |
| `src/agent/prompts.py` L1–48 | System prompt manifest rules & safety constraints |
| `src/agent/prompts.py` L166–249 | `submit_manifest` tool schema (what Claude sees) |
| `src/verification/checks.py` | Pre-execution verification pipeline: grounding, constraints, confidence, conflict |
| `src/verification/icd10.py` | ICD-10 / CPT regex validators |
| `src/api/main.py` L129–178 | HTTP endpoints: approve, execute, get manifest |
| `src/api/schemas.py` | API request/response schemas for manifest flow |
| `src/tools/registry.py` L25–36 | Lightweight duplicate ManifestItem/ChangeManifest for the registry layer |
| `tests/conftest.py` L15–35 | Shared fixture: sample ManifestItem and ChangeManifest |
| `tests/unit/test_models.py` L18–80 | Unit tests for model defaults, explicit fields, enum values |
| `tests/unit/test_verification.py` | Verification check tests: grounding, constraints, confidence, conflict, full pipeline |
| `tests/eval/dataset.json` L530–544 | Adversarial case: attempt to bypass manifest approval |

---

## 2. The Pydantic Models

### 2.1 ManifestAction (Enum)

```python
# src/agent/models.py L23-26
class ManifestAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
```

Simple string enum. The `str` base allows direct JSON serialisation. Tests confirm round-trip: `ManifestAction("create") is ManifestAction.CREATE`.

### 2.2 ManifestItem

```python
# src/agent/models.py L29-39
class ManifestItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    resource_type: str              # FHIR resource type, e.g. "MedicationRequest"
    action: ManifestAction          # create / update / delete
    proposed_value: dict[str, Any]  # the new FHIR resource body
    current_value: dict[str, Any] | None = None  # only for update/delete
    source_reference: str           # e.g. "Encounter/5", "MedicationRequest/456"
    description: str                # human-readable explanation
    confidence: str = "high"        # "high" | "medium" | "low"
    status: str = "pending"         # "pending" → "approved" → "completed" | "rejected" | "failed"
    depends_on: list[str] = Field(default_factory=list)  # IDs of prerequisite ManifestItems
```

**Key design notes:**
- `id` auto-generates a UUID on construction — callers never need to supply one
- `confidence` is a free-form string defaulting to `"high"`, but the tool schema constrains it to `enum: ["high", "medium", "low"]` (prompts.py L224–228)
- `status` is also free-form, not an enum — values are managed by convention across loop.py and api/main.py
- `source_reference` format is `"ResourceType/ID"` — validated by regex in `check_grounding` (`^(\w+)/(.+)$`)
- `depends_on` is a list of ManifestItem IDs, not resource references — this is used for topological ordering

### 2.3 ChangeManifest

```python
# src/agent/models.py L42-48
class ChangeManifest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    patient_id: str
    encounter_id: str | None = None
    items: list[ManifestItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = "draft"  # "draft" → "executing" → "completed" | "failed"
```

**Key design notes:**
- `encounter_id` is optional; `_build_manifest` falls back to `session.page_context.encounter_id` if not in LLM arguments (loop.py L426–430)
- `status` tracks the manifest as a whole, separate from per-item `status`
- `created_at` uses `datetime.utcnow` (note: deprecated in Python 3.12+, but functional)

### 2.4 Duplicate Models in Registry Layer

`src/tools/registry.py` L25-36 defines a **separate, simpler** `ManifestItem` and `ChangeManifest`:

```python
class ManifestItem(BaseModel):
    id: str
    action: str                    # plain string, not ManifestAction enum
    resource_type: str
    summary: str                   # differs from agent models which use 'description'
    payload: dict[str, Any] = ...  # differs from agent models which use 'proposed_value'
    approved: bool = False         # boolean flag, not status string

class ChangeManifest(BaseModel):
    items: list[ManifestItem] = ...  # no patient_id, no encounter_id, no created_at
```

**⚠ Edge case worth noting:** These two model families are NOT the same. The registry's `ManifestItem` uses `summary`/`payload`/`approved` while the agent's uses `description`/`proposed_value`/`status`. The agent loop uses `src/agent/models.py`; the registry uses its own. The `tool_submit_manifest` in the registry validates against its own lighter schema. The `submit_manifest` tool call in `loop.py` uses `_build_manifest` which creates `src/agent/models.ManifestItem` directly.

---

## 3. Lifecycle of a Proposed FHIR Write

### Phase diagram

```
planning → [LLM calls fhir_read, gathers data]
         → [LLM calls submit_manifest]
         → reviewing (session.phase = "reviewing", manifest attached)
         → [Clinician reviews via API]
         → [POST /api/manifest/{id}/approve with approved_items/rejected_items]
         → [POST /api/manifest/{id}/execute]
         → executing (session.phase = "executing")
         → complete (session.phase = "complete")
```

### Step-by-step flow:

**Step 1: LLM builds the manifest** (loop.py L219-236)
- LLM calls `submit_manifest` tool with `patient_id`, optional `encounter_id`, and `items` array.
- `_build_manifest()` (loop.py L404-432) parses raw JSON into `ManifestItem` instances.
- Each item starts with `status="pending"`.
- Manifest stored on `session.manifest`, phase set to `"reviewing"`.
- Agent loop **breaks** (loop.py L116-117): `if session.phase == "reviewing": break`.

**Step 2: Clinician reviews** (api/main.py L129-157)
- `POST /api/manifest/{session_id}/approve` receives `ApprovalRequest` with `approved_items` and `rejected_items` (lists of item IDs).
- Items are marked `"approved"` or `"rejected"` by iterating `session.manifest.items`.
- If any items are approved, `verify_manifest()` runs the verification pipeline.
- Returns `ApprovalResponse` with verification results and overall `passed` boolean.

**Step 3: Verification pipeline** (verification/checks.py L257-269)
- For each item: `check_grounding` → `check_constraints` → `check_confidence` → `check_conflict`
- Aggregated into a `VerificationReport`.
- `report.passed` is True only if **no results with severity "error" have failed** — warnings don't block.

**Step 4: Execution** (loop.py L253-312 / api/main.py L160-179)
- `POST /api/manifest/{session_id}/execute` calls `agent_loop.execute_approved(session)`.
- `execute_approved` sets phase to `"executing"`, manifest status to `"executing"`.
- **Topological sort** on items.
- Iterates sorted items, skips non-approved ones.
- DELETE action: uses `api_request` with DELETE method to `/fhir/{type}/{id}`.
- CREATE/UPDATE action: uses `fhir_write`.
- On success: item status → `"completed"`.
- On failure: item status → `"failed"`, manifest status → `"failed"`, phase → `"complete"`, **returns immediately** (fail-fast, no rollback).
- On all success: manifest status → `"completed"`, phase → `"complete"`, summary message appended.

### Alternative write path — during LLM loop (loop.py L170-192)

The `fhir_write` tool can also be called directly by the LLM during the agent loop. This path requires `manifest_item_id` and checks `_is_item_approved()`. If the item isn't approved, returns an error: `"Error: manifest item is not approved. Writes require clinician approval first."` This path is **not the primary flow** — it exists for the case where the LLM tries to write item-by-item during conversation, but the system prompt discourages it.

---

## 4. Action Types

| Action | Execution path (execute_approved) | Notes |
|--------|-----------------------------------|-------|
| `create` | `fhir_write(resource_type, proposed_value)` | Proposed value is the full new FHIR resource |
| `update` | `fhir_write(resource_type, proposed_value)` | `current_value` stores the pre-change state for conflict detection |
| `delete` | `api_request(endpoint="/fhir/{type}/{id}", method="DELETE")` | Extracts `id` from `proposed_value.get('id', '')` |

**Note on delete:** The ID comes from `item.proposed_value.get('id', '')` — meaning for deletes, the `proposed_value` dict must contain an `id` field. This is a slightly awkward convention since "proposed_value" for a deletion is really "value to delete".

---

## 5. Source References for Grounding

`source_reference` is a required string on every ManifestItem (required in the tool schema, L237-244). Format: `"ResourceType/ID"` (e.g., `"Encounter/5"`, `"MedicationRequest/456"`).

The system prompt enforces this:
> "Every item must have a `source_reference` pointing to the FHIR resource that justifies it."

### Grounding verification (checks.py L54-102)

```python
async def check_grounding(item, openemr_client):
    # 1. Rejects empty source_reference
    # 2. Validates format with regex: ^(\w+)/(.+)$
    # 3. Fetches the resource via openemr_client.read(resource_type, resource_id)
    # 4. Passes only if the resource actually exists in the EMR
```

**What it catches:**
- Empty string → fails ("No source_reference provided")
- Invalid format like `"not-a-valid-ref"` → fails ("Invalid source_reference format")
- Valid format but resource doesn't exist → fails ("not found")
- Connection errors → fails ("Failed to fetch")

---

## 6. Confidence Levels

Three levels: `"high"` (default), `"medium"`, `"low"`.

### Hedging detection (checks.py L182-204)

```python
HEDGING_PHRASES = [
    "possibly", "might be", "unclear", "uncertain",
    "maybe", "could be", "not sure",
]
```

`check_confidence` concatenates `item.description` + JSON-serialised `proposed_value`, lower-cases, and scans for these phrases. If found: `passed=False, severity="warning"`.

**Important:** Hedging check has severity `"warning"`, not `"error"`. This means hedging language **does not block** the manifest from passing verification (see `VerificationReport.passed` — only severity `"error"` failures block).

From the system prompt: "When uncertain, flag manifest items as 'medium' or 'low' confidence." This is guidance to the LLM to self-label, separate from the automated hedging scan.

---

## 7. Topological Sort on `depends_on`

```python
# src/agent/loop.py L456-476
def _topological_sort(self, items: list[ManifestItem]) -> list[ManifestItem]:
    item_map = {item.id: item for item in items}
    visited: set[str] = set()
    result: list[ManifestItem] = []

    def visit(item_id: str) -> None:
        if item_id in visited:
            return
        visited.add(item_id)
        item = item_map.get(item_id)
        if item is None:
            return
        for dep_id in item.depends_on:
            visit(dep_id)
        result.append(item)

    for item in items:
        visit(item.id)
    return result
```

**Algorithm:** Classic DFS topological sort. For each item, recursively visit all dependencies first, then append self. The `visited` set prevents re-processing.

**Called from:** `execute_approved` (loop.py L264) — items are sorted before the execution loop.

**Edge cases (documented in docs/research_s2_orchestration_loop.md L222):**
1. **No cycle detection.** A circular `depends_on` chain is silently broken by the `visited` set. The ordering among cycle members becomes arbitrary — whichever node is visited first "wins" and goes first. No error is raised.
2. **Dangling references.** If `depends_on` contains an ID not in `item_map`, the `visit` call hits `item_map.get(item_id) → None` and returns silently. The dependency is effectively ignored.
3. **Non-approved items still get sorted.** The sort operates on ALL items; the approval filter happens AFTER in the iteration: `if item.status != "approved": continue`. So a non-approved dependency still satisfies ordering — it just won't execute.

**Use case for `depends_on`:** A medication adjustment that requires a new encounter first, or a diagnosis that depends on an observation being recorded. The LLM is instructed (system prompt L37): "Items with dependencies must declare them in `depends_on`."

---

## 8. Suggested Running Example — Maria Santos Medication Adjustment

### Scenario (based on eval dataset hp-14)

Dr. Chen says: *"Increase metformin to 1000mg twice daily for this patient due to worsening A1c."*

Page context: `patient_id="1"` (Maria Santos), `encounter_id="2"`, `page_type="medications"`.

### Expected manifest:

```python
ChangeManifest(
    patient_id="1",
    encounter_id="2",
    items=[
        ManifestItem(
            resource_type="MedicationRequest",
            action=ManifestAction.UPDATE,
            proposed_value={
                "resourceType": "MedicationRequest",
                "id": "med-123",  # existing metformin ID from fhir_read
                "medicationCodeableConcept": {
                    "coding": [{"code": "860975", "system": "http://www.nlm.nih.gov/research/umls/rxnorm", "display": "Metformin 1000mg"}]
                },
                "dosageInstruction": [{
                    "text": "1000mg twice daily",
                    "timing": {"repeat": {"frequency": 2, "period": 1, "periodUnit": "d"}}
                }],
                "subject": {"reference": "Patient/1"},
            },
            current_value={...},  # previous metformin record from fhir_read
            source_reference="MedicationRequest/med-123",
            description="Increase metformin dosage from 500mg to 1000mg twice daily due to worsening A1c",
            confidence="high",
            depends_on=[],
        )
    ],
)
```

### Multi-item example with dependencies (hypothetical, good for illustrating topo sort):

If the adjustment also requires recording a new A1c observation first:

```python
items=[
    ManifestItem(
        id="item-a1c",
        resource_type="Observation",
        action=ManifestAction.CREATE,
        proposed_value={"code": {"coding": [{"code": "4548-4"}]}, "valueQuantity": {"value": 8.2}},
        source_reference="Encounter/2",
        description="Record latest A1c result of 8.2%",
        confidence="high",
        depends_on=[],
    ),
    ManifestItem(
        id="item-med",
        resource_type="MedicationRequest",
        action=ManifestAction.UPDATE,
        proposed_value={...},
        source_reference="MedicationRequest/med-123",
        description="Increase metformin to 1000mg BID based on A1c 8.2%",
        confidence="high",
        depends_on=["item-a1c"],  # must create observation before adjusting med
    ),
]
```

Topological sort ensures `item-a1c` executes before `item-med`.

---

## 9. Verification Pipeline Detail

### Four checks run per item (verify_manifest, checks.py L257-269):

| Check | Async? | Severity on failure | What it validates |
|-------|--------|-------------------|-------------------|
| `check_grounding` | Yes | `error` | source_reference exists in EMR |
| `check_constraints` | No | `error` or `warning` | ICD-10/CPT format, SOAP sections |
| `check_confidence` | No | `warning` | No hedging language in description/proposed_value |
| `check_conflict` | Yes | `error` | Target resource unchanged since manifest was built |

### VerificationReport

```python
class VerificationReport(BaseModel):
    manifest_id: str
    results: list[VerificationResult] = []

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results if r.severity == "error")

    @property
    def warnings(self) -> list[VerificationResult]:
        return [r for r in self.results if r.severity == "warning"]
```

**Key insight:** `passed` only considers `severity="error"` results. Warning-level failures (hedging language, missing SOAP sections) do NOT block the manifest. This is a deliberate design: hedging is informational, not a hard stop.

---

## 10. Edge Cases & Surprising Behaviour

### 10.1 `target_resource_id` doesn't exist on ManifestItem
`check_conflict` (checks.py L211) accesses `item.target_resource_id`, but `ManifestItem` has no such field. The tests work around this with `object.__setattr__(item, "target_resource_id", ...)` (test_verification.py L308, L326). In production, this would raise `AttributeError` unless the attribute is set dynamically. The full pipeline test patches it too (L342-343). **This is likely a latent bug or unfinished feature.**

### 10.2 Two ManifestItem models
The agent models (`src/agent/models.py`) and registry models (`src/tools/registry.py`) define different `ManifestItem` classes with different field names (`description` vs `summary`, `proposed_value` vs `payload`, `status` vs `approved`). The agent loop uses one; the registry's `tool_submit_manifest` uses the other. They serve different layers but the naming collision could confuse readers.

### 10.3 No rollback on partial execution failure
`execute_approved` (loop.py L289-296): if any item fails, it marks the item `"failed"` and the manifest `"failed"`, then returns immediately. Previously completed items are NOT rolled back. FHIR resources that were already written stay written.

### 10.4 Topological sort has no cycle detection
Documented in the research notes (docs/research_s2_orchestration_loop.md L222). Circular dependencies are silently broken by the `visited` set. No error or warning.

### 10.5 Dangling depends_on references are silently ignored
If a `depends_on` ID doesn't match any item in the manifest, `item_map.get(item_id)` returns `None` and `visit()` returns without error. The dependent item executes anyway, potentially out of order relative to external expectations.

### 10.6 `datetime.utcnow()` deprecation
`ChangeManifest.created_at` uses `datetime.utcnow`, deprecated since Python 3.12. Should be `datetime.now(datetime.UTC)`.

### 10.7 The approval endpoint runs verification but doesn't gate execution
`POST /approve` runs `verify_manifest` and returns results, but the subsequent `POST /execute` doesn't re-check — it trusts that the client inspected the report. A client could approve and execute even if verification failed.

### 10.8 fhir_write requires manifest_item_id in the tool schema but not in execute_approved
The tool schema (prompts.py L121) marks `manifest_item_id` as required for `fhir_write`. But `execute_approved` calls `fhir_write` directly (loop.py L277-279) without a manifest_item_id — it bypasses the tool dispatch entirely by calling `openemr_client.fhir_write` directly.

### 10.9 Fail-fast prevents reordering on failure
If item B depends on item A, and item A fails, item B (which comes after in topo order) will still be attempted because the skip logic only checks `status != "approved"`, not whether dependencies succeeded. **Wait — re-reading:** Actually, on failure the method returns immediately (L296), so item B would never be reached. The fail-fast is at the manifest level, not item level.

---

## 11. Eval Dataset Entries Relevant to Manifests

| ID | Description | Expected |
|----|-------------|----------|
| `hp-14` | Update metformin dosage for patient 1 | `fhir_read` + `submit_manifest`, manifest item: MedicationRequest/update |
| `adv-04` | Attempt to bypass manifest approval | Should refuse, no manifest items |
| `hp-05` | Document a new diagnosis | `fhir_read` + `submit_manifest`, manifest item: Condition/create |

---

## 12. Suggested Structure for §5

1. **Open with the problem:** Why not just write to FHIR directly? Safety. Clinical writes need human review.
2. **Introduce the models:** ManifestItem → ChangeManifest. Show the fields. Use Maria Santos metformin example as concrete JSON.
3. **Walk through the lifecycle:** Planning → submit_manifest → reviewing → approve → verify → execute → complete. Annotate with phase transitions.
4. **Zoom in on source_reference and grounding:** How the agent cites evidence, how verification confirms it exists.
5. **Confidence levels and hedging:** The self-label vs. automated scan distinction.
6. **depends_on and topological sort:** Multi-item example (A1c observation → medication update). Show the DFS algorithm. Note the no-cycle-detection edge case.
7. **Verification pipeline:** Four checks, severity distinction, warnings-don't-block.
8. **Fail-fast execution:** No rollback, no partial commits visible in API response.
