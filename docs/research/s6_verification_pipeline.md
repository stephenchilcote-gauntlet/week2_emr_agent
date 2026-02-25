# §6 Research Notes — The Verification Pipeline

## 1. Architecture Overview

The verification pipeline lives in `src/verification/` (two files: `checks.py` and `icd10.py`).
It is a **four-check, per-item sweep** that runs against every `ManifestItem` in a `ChangeManifest`.
It is invoked exactly once in the codebase: the `/api/manifest/{session_id}/approve` endpoint
(`src/api/main.py:146`).

The pipeline does NOT run during manifest *construction* (the agent loop in `src/agent/loop.py`
builds manifests unverified). It runs **after** the clinician marks items approved/rejected,
but **before** execution begins (the `/api/manifest/{session_id}/execute` endpoint is a separate
call). The approval endpoint returns the `VerificationReport` to the caller; the `passed` boolean
gates whether the frontend should allow execution to proceed.

```
User ─► /api/chat (agent builds manifest) ─► /api/manifest/.../approve (VERIFICATION HERE)
                                                 │
                                                 ├─ passed=True  ─► frontend enables /execute
                                                 └─ passed=False ─► frontend blocks /execute
```

**Important**: the gating is **advisory, not enforced server-side**. The `/execute` endpoint
(`src/api/main.py:160-179`) does NOT re-check `report.passed`. A caller could POST to `/execute`
despite a failed verification. The agent loop's `execute_approved()` method does check
`item.status == "approved"` but not verification status. This is a design choice worth calling out.

---

## 2. Data Model

### 2.1 `VerificationResult` (`checks.py:25-32`)

```python
class VerificationResult(BaseModel):
    item_id: str          # links back to the ManifestItem.id
    check_name: str       # e.g. "grounding", "constraint_icd10", "confidence", "conflict"
    passed: bool
    message: str          # human-readable explanation
    severity: str = "error"   # "error" | "warning" | "info"
```

### 2.2 `VerificationReport` (`checks.py:35-51`)

```python
class VerificationReport(BaseModel):
    manifest_id: str
    results: list[VerificationResult]

    @property
    def passed(self) -> bool:
        # ONLY severity="error" results can block passage.
        # Warnings and info are ignored.
        return all(r.passed for r in self.results if r.severity == "error")

    @property
    def warnings(self) -> list[VerificationResult]:
        return [r for r in self.results if r.severity == "warning"]
```

**Key design decision**: `passed` filters on `severity == "error"`. A report can contain
failed results with `severity="warning"` and still pass. This is the error-vs-warning distinction.

### 2.3 Severity assignments by check type

| Check              | Default severity | Can fail without blocking? |
|--------------------|-----------------|---------------------------|
| `grounding`        | `"error"`       | No — blocks execution     |
| `constraint_icd10` | `"error"`       | No                        |
| `constraint_cpt`   | `"error"`       | No                        |
| `constraint_document_sections` | `"warning"` | Yes — warns only  |
| `confidence`       | `"warning"`     | Yes — warns only          |
| `conflict`         | `"error"`       | No — blocks execution     |

Note: the confidence check on pass returns `severity="info"`, and document section check on pass
also returns `"info"`. These "info" results are always `passed=True` so they never affect the gate.

---

## 3. The Four Checks (detailed)

### 3.1 Grounding Check (`check_grounding`, lines 54-102)

**Purpose**: Verify the LLM's cited `source_reference` actually exists in OpenEMR.

**Mechanism**:
1. If `source_reference` is empty/falsy → immediate fail.
2. Regex-parse as `ResourceType/ID` (`r"^(\w+)/(.+)$"`).
3. Attempt `await openemr_client.read(resource_type, resource_id)`.
4. If `None` returned → fail ("not found"). If exception → fail ("Failed to fetch").
5. If resource returned → pass.

**Source excerpt** (`checks.py:70-77`):
```python
match = re.match(r"^(\w+)/(.+)$", item.source_reference)
if not match:
    return VerificationResult(
        item_id=item.id,
        check_name="grounding",
        passed=False,
        message=f"Invalid source_reference format: {item.source_reference}",
    )
```

**Edge cases / notes**:
- The regex `(\w+)/(.+)` accepts `_` in resource type and anything (including `/`) in the ID
  portion. So `Encounter/uuid/subpath` would match with `resource_id = "uuid/subpath"`.
- Network errors are caught and result in a fail, not an exception propagation.
  The check is **tolerant of infrastructure failures** — it doesn't crash the pipeline.
- Every `ManifestItem` has `source_reference: str` (not Optional), but an empty string triggers
  the falsy path.

**Good example for prose**: A manifest item claiming to be based on `Encounter/5` gets verified
by fetching that encounter from FHIR. If the LLM hallucinated the reference, this catches it.

---

### 3.2 Constraint Checks (`check_constraints`, lines 105-179)

**Purpose**: Validate domain-specific format rules on `proposed_value`.

**Mechanism**: Three sub-checks triggered by item characteristics:

#### 3.2a ICD-10 validation (Condition resources with `code` field)

```python
if item.resource_type == "Condition" and "code" in proposed:
    code = _extract_code(proposed["code"])
    if code and not validate_icd10_format(code):
        # → fail
```

The regex (`icd10.py:7`):
```python
ICD10_PATTERN = re.compile(r"^[A-Z]\d{2}(\.\d{1,4})?$")
```
Format: `[A-Z] + 2 digits + optional (dot + 1-4 digits)`. Examples: `E11.9`, `I10`, `J45.909`.

**Note**: `validate_icd10_format` calls `.strip().upper()` on input, so lowercase codes pass.
This is tested explicitly — the test notes `"e11.9"` should pass. This is a deliberate
case-insensitivity choice.

#### 3.2b CPT validation (Procedure resources with `code` field)

```python
CPT_PATTERN = re.compile(r"^\d{5}$")
```
Exactly 5 digits. No letters, no decimals. Examples: `99213`, `00100`.

**Note**: Real CPT codes can include letter suffixes (e.g., Category III codes like `0213T`).
The regex rejects these. This is a simplification — worth flagging in prose as a pragmatic
constraint rather than full CPT specification compliance.

#### 3.2c Clinical document section validation

Triggered when `proposed_value` contains `"document"` or `"text"` keys.
Checks for the presence of four SOAP note section keywords (case-insensitive substring match):

```python
required_sections = ["subjective", "objective", "assessment", "plan"]
doc_lower = doc.lower()
missing = [s for s in required_sections if s not in doc_lower]
```

This check has `severity="warning"` — it never blocks execution. Missing sections produce a
warning listing which are absent.

**Edge case**: The check is a naive substring search. A document containing the word "objectively"
would satisfy the "objective" section check. Similarly, "subjective" and "subject" overlap.
This is adequate for catching gross omissions but not precise section parsing.

#### 3.2d `_extract_code` helper (lines 272-283)

Handles FHIR's `CodeableConcept` structure:
```python
def _extract_code(code_value: Any) -> str | None:
    if isinstance(code_value, str):
        return code_value
    if isinstance(code_value, dict):
        if "coding" in code_value and isinstance(code_value["coding"], list):
            for coding in code_value["coding"]:
                if isinstance(coding, dict) and "code" in coding:
                    return coding["code"]  # returns FIRST match
        if "code" in code_value:
            return code_value["code"]
    return None
```

**Note**: Returns the *first* coding entry's code. If a CodeableConcept has multiple coding
systems (e.g., ICD-10 + SNOMED), only the first is validated. The second is silently ignored.

#### 3.2e When no checks fire

If a `ManifestItem` is e.g. `resource_type="Observation"` with no `code`, `document`, or `text`
keys, `check_constraints` returns an **empty list**. No results are added to the report at all.
The item is neither passed nor failed on constraints — it's simply not checked.

---

### 3.3 Confidence Check (`check_confidence`, lines 182-204)

**Purpose**: Detect hedging language that indicates the LLM was uncertain.

**Mechanism**: Concatenate `item.description` and `json.dumps(item.proposed_value)`,
lowercase the whole thing, then scan for any of 7 hedging phrases:

```python
HEDGING_PHRASES = [
    "possibly", "might be", "unclear", "uncertain",
    "maybe", "could be", "not sure",
]
```

```python
text_to_check = item.description.lower()
text_to_check += " " + json.dumps(item.proposed_value).lower()
found = [phrase for phrase in HEDGING_PHRASES if phrase in text_to_check]
```

**Severity**: Always `"warning"` on failure, `"info"` on pass. **Never blocks execution.**

**Edge cases**:
- `json.dumps` of proposed_value means dict keys are also scanned. A key named `"possibly_related"`
  would trigger the "possibly" match.
- Substring matching: "impossibly" contains "possibly", would trigger.
- The check scans the LLM's `description` field, not the raw LLM response. The description is
  what the agent chose to write as human-readable explanation. This is the right target — it catches
  the agent hedging in its own words.
- Checks `proposed_value` as JSON text. If the value contains clinical text like
  `"note": "patient might be allergic"`, that also triggers. This is intentional — hedging in
  the *data itself* is also suspicious.

**Good example for prose**: If the agent writes `description="Patient possibly has hypertension"`,
the confidence check fires a warning with message
`"Low confidence — hedging language detected: possibly"`.

---

### 3.4 Conflict Detection (`check_conflict`, lines 207-254)

**Purpose**: Detect concurrent modifications — has the target resource changed since the manifest
was built?

**Mechanism**:
1. If no `target_resource_id` or no `current_value` → skip (pass with `severity="info"`).
2. Re-read the live resource: `await openemr_client.read(resource_type, target_resource_id)`.
3. If resource deleted (returns `None`) → fail.
4. Compare `live_data != item.current_value` — a simple Python dict inequality check.
5. If different → conflict detected (fail, severity="error").

```python
live_data = live if isinstance(live, dict) else {}
if live_data != item.current_value:
    return VerificationResult(
        item_id=item.id,
        check_name="conflict",
        passed=False,
        message=(
            f"Conflict detected: {item.resource_type}/{item.target_resource_id} "
            "has been modified since the manifest was built"
        ),
    )
```

**Critical edge case / known issue**: `ManifestItem` does NOT define `target_resource_id` as a
field (see `src/agent/models.py:29-39`). The check accesses `item.target_resource_id` (line 211)
which will raise `AttributeError` on a standard Pydantic model. The tests work around this with
`object.__setattr__(item, "target_resource_id", "42")`. The `verify_manifest` integration test
patches all items with `target_resource_id = None` so the guard clause skips the check.

This means **conflict detection is effectively inert in production** for items built through
`_build_manifest()` in `loop.py` — they won't have `target_resource_id`, so `check_conflict`
either skips (if the attribute is missing and happens to be falsy) or crashes. The test suite
explicitly documents this:

```python
# test_verification.py:284-286
# ManifestItem has no target_resource_id field, so accessing it
# will raise AttributeError — check_conflict guards with hasattr-like logic.
# Since the model doesn't define target_resource_id, this will raise.
```

**This is worth highlighting in prose as an incomplete feature / latent bug.**

**Comparison semantics**: Uses `!=` on dicts. No deep/structural comparison, no ignoring of
metadata fields like `meta.lastUpdated`. If the FHIR server adds a `meta.versionId` between
reads, that alone would trigger a false conflict.

---

## 4. Pipeline Orchestration (`verify_manifest`, lines 257-269)

```python
async def verify_manifest(
    manifest: ChangeManifest, openemr_client: Any
) -> VerificationReport:
    report = VerificationReport(manifest_id=manifest.id)
    for item in manifest.items:
        report.results.append(await check_grounding(item, openemr_client))
        report.results.extend(check_constraints(item))
        report.results.append(check_confidence(item))
        report.results.append(await check_conflict(item, openemr_client))
    return report
```

**Key observations**:
- Sequential per-item, sequential across checks. No parallelism (`asyncio.gather` not used).
- `check_constraints` returns a `list` (→ `extend`), others return single results (→ `append`).
- All items are checked, regardless of their `status` (approved/rejected/pending). This means
  rejected items are also verified — possibly wasteful but simpler.
- No short-circuiting: a failing grounding check doesn't skip the remaining checks for that item.
  All four checks always run, giving a complete diagnostic picture.

---

## 5. How the Pipeline Gates Execution

### 5.1 The approval endpoint (`src/api/main.py:129-157`)

```python
@app.post("/api/manifest/{session_id}/approve")
async def approve_manifest(session_id: str, req: ApprovalRequest):
    session = _get_session(session_id)
    # ... mark items approved/rejected ...

    has_approved = any(item.status == "approved" for item in session.manifest.items)
    if has_approved:
        report = await verify_manifest(session.manifest, openemr_client)
    else:
        report = VerificationReport(manifest_id=session.manifest.id)  # empty, passes trivially

    return ApprovalResponse(
        session_id=session.id,
        manifest_id=session.manifest.id,
        results=[r.model_dump() for r in report.results],
        passed=report.passed,
    )
```

**Key detail**: Verification only runs if at least one item was approved. If all items are
rejected, the response contains an empty report that trivially passes (`all()` on empty = True).

### 5.2 The execution endpoint (`src/api/main.py:160-179`)

The execute endpoint does NOT check `report.passed`. It simply iterates approved items and writes
them. The verification gate is enforced by the **client** (frontend) choosing not to call execute
when `passed=False`.

### 5.3 The `execute_approved` method (`src/agent/loop.py:253-312`)

Uses topological sort on `depends_on` to order execution. Stops on first failure (sets
`manifest.status = "failed"` and returns). Does NOT re-verify.

---

## 6. Observability Integration

`src/observability/tracing.py:144-191` provides a `trace_verification` decorator that creates
OTEL spans with attributes: `verification.check_name`, `verification.passed`,
`verification.item_count`. However, **this decorator is not currently applied** to any of the
check functions in `checks.py`. The functions are called directly. The decorator exists as
infrastructure but is unused — another incomplete integration worth noting.

---

## 7. Test Coverage Summary

`tests/unit/test_verification.py` — 414 lines, 6 test classes:

| Class | What it covers | Notable |
|-------|---------------|---------|
| `TestICD10Validation` | 7 valid + 8 invalid codes | Explicitly tests lowercase passes |
| `TestCPTValidation` | 3 valid + 5 invalid codes | |
| `TestConfidenceCheck` | No hedging, hedging in desc, hedging in value, all 7 phrases | Parametrized |
| `TestConstraintValidation` | ICD-10 valid/invalid, CPT valid/invalid, no-code, SOAP missing/present | |
| `TestGroundingCheck` | Pass, not found, no ref, bad format, client error | |
| `TestConflictCheck` | No target (works around missing field), conflict detected, no conflict | Uses `object.__setattr__` hack |
| `TestVerifyManifest` | Full pipeline with patched `target_resource_id` | Asserts check_names present |
| `TestVerificationReport` | `passed` property, `warnings` property, empty report | |

---

## 8. Suggested Examples for Prose

1. **Grounding check catching a hallucination**: Item with `source_reference="Encounter/999"` where
   encounter 999 doesn't exist. The FHIR read returns `None`. Verification fails with
   `"Source resource Encounter/999 not found"`.

2. **ICD-10 format rejection**: LLM proposes a Condition with `code: "DIABETES"`. The regex
   `^[A-Z]\d{2}(\.\d{1,4})?$` doesn't match. Verification returns
   `"Invalid ICD-10 format: DIABETES"`.

3. **Warning vs error contrast**: A SOAP note missing the "Assessment" section produces a
   *warning* (`severity="warning"`) — the report still passes. But an invalid ICD-10 code produces
   an *error* (`severity="error"`) — the report fails. Same manifest, different outcomes.

4. **Hedging detection on LLM text**: `description="Patient possibly has hypertension"` → warning
   with `"Low confidence — hedging language detected: possibly"`. The clinician sees the warning
   but can still proceed.

5. **Conflict detection**: Between the time the agent reads a patient's condition and the clinician
   approves the change, another clinician updates the same condition. The re-read returns different
   data → `"Conflict detected"`.

---

## 9. Edge Cases & Surprising Behaviour Worth Noting

1. **`target_resource_id` doesn't exist on the model** — conflict detection is structurally broken
   for items built through the normal agent flow. Tests use `object.__setattr__` to work around it.
   This is the most significant latent issue in the pipeline.

2. **No server-side enforcement** — verification is advisory. The `/execute` endpoint doesn't check
   `report.passed`. A malicious or buggy client can execute a failed manifest.

3. **Empty `all()` returns True** — an empty report (no approved items, or a resource type that
   triggers no constraint checks) always passes. This is Python semantics but could surprise readers.

4. **`_extract_code` returns only the first coding** — multi-system CodeableConcepts
   (ICD-10 + SNOMED) only validate the first code.

5. **Substring hedging matches** — "impossibly" triggers "possibly", "unclear" matches inside
   "nuclear". Dictionary keys in proposed_value are also scanned.

6. **Conflict detection uses shallow dict equality** — FHIR metadata fields (`meta.lastUpdated`,
   `meta.versionId`) that change on any server touch will cause false positives.

7. **All items verified, not just approved ones** — rejected items go through all four checks too.

8. **Sequential execution** — checks run sequentially per item, items run sequentially. For a
   manifest with many items, grounding and conflict checks make 2 FHIR calls per item (one for
   grounding, one for conflict), all awaited in series.

9. **`trace_verification` decorator exists but is unused** — observability infrastructure is
   defined but not wired up to the actual check functions.

10. **CPT regex rejects Category III codes** — Real CPT includes codes like `0213T` (letter
    suffix). The `^\d{5}$` pattern is narrower than the full CPT specification.
