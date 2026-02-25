# §2 Research Notes — The Orchestration Loop

> Research for prose author. Not reader-facing prose.

---

## 1. Key Source Files

| File | Role |
|---|---|
| `src/agent/loop.py` | **The AgentLoop class** — the core orchestrator |
| `src/agent/models.py` | Pydantic data models: `AgentSession`, `AgentMessage`, `ToolCall`, `ToolResult`, `ChangeManifest`, `ManifestItem`, `PageContext` |
| `src/agent/prompts.py` | System prompt + `TOOL_DEFINITIONS` (5 tools) |
| `src/api/main.py` | FastAPI HTTP layer — entry point that calls `AgentLoop.run()` |
| `src/verification/checks.py` | Post-manifest verification (grounding, constraints, confidence, conflict) |
| `src/tools/registry.py` | Alternative tool dispatch (registry pattern); coexists with loop's built-in dispatch |

---

## 2. The Four-Phase State Machine

Defined on `AgentSession.phase` (string field, default `"planning"`).

```python
# src/agent/models.py:70
class AgentSession(BaseModel):
    ...
    phase: str = "planning"
```

### Phase transitions observed in code:

| From | To | Trigger | Code location |
|---|---|---|---|
| `planning` | `reviewing` | `submit_manifest` tool is executed | `loop.py:222` — `session.phase = "reviewing"` |
| `reviewing` | `executing` | `execute_approved()` is called | `loop.py:261` — `session.phase = "executing"` |
| `executing` | `complete` | All approved items finish (success or failure) | `loop.py:295-299` |

### Key observations:

- **Phase is a plain string, not an enum.** No validation; any value accepted. The tests confirm `"planning"`, `"reviewing"`, `"executing"`, `"complete"` (see `test_models.py:105-113`).
- **There is no transition from `planning` → `complete` for read-only queries.** When the user asks a question that requires no writes (e.g., "What is Maria's HbA1c?"), the loop finishes but `session.phase` stays `"planning"`. The phase only moves when a manifest is submitted. This is an important subtlety.
- **The `"reviewing"` phase causes the loop to break immediately** (`loop.py:116-117`) — the agent stops taking actions and waits for clinician approval.

---

## 3. The Core Loop — `AgentLoop.run()`

```python
# src/agent/loop.py:73-130
async def run(self, session: AgentSession, user_message: str) -> AgentSession:
```

### Step-by-step walkthrough:

1. **Append user message** to `session.messages` (line 81-83)
2. **Enter the for-loop** — `for _round in range(MAX_TOOL_ROUNDS):` (line 85) — **ceiling is 15 rounds**
3. **Call LLM** — `response = await self._call_llm(session)` (line 86)
4. **Extract tool calls and text** from response (lines 88-89)
5. **Branch on tool calls:**
   - **No tool calls → terminal response.** Append assistant message, `break`. (lines 91-95)
   - **Has tool calls → execute them sequentially.** (lines 97-114)
6. **After tool execution, check phase** — if `"reviewing"`, `break`. (lines 116-117)
7. **If the for-loop exhausts** (15 rounds, no natural stop), the `else` clause fires and appends a polite "max rounds reached" message (lines 118-128).

### The "interleaving" pattern:

Each round is one LLM call + tool execution batch. The sequence is strictly:
```
LLM → extract tools → execute tools → LLM → extract tools → ...
```

Tool calls within a single round are executed **sequentially** (not parallel):
```python
for tc in tool_calls:
    result = await self._execute_tool(tc, session)
```

This is notable because Claude can return multiple tool_use blocks in one response. They run one at a time.

---

## 4. MAX_TOOL_ROUNDS = 15

```python
# src/agent/loop.py:22
MAX_TOOL_ROUNDS = 15
```

- **What counts as one round:** One LLM API call + the execution of all tool calls in that response.
- **The counter doesn't distinguish between read and write tools.**
- **Exhaustion message:**
  ```python
  "I've reached the maximum number of tool calls for this turn.
   Please provide more guidance or simplify the request."
  ```
- **Edge case:** If the model calls `submit_manifest` on round 15, the manifest IS created and phase transitions to `"reviewing"` — the `break` at line 117 fires before the `else` clause. The for/else only triggers if ALL 15 rounds finish without a break.

---

## 5. The Five Tools

Defined in `src/agent/prompts.py:64-249` as `TOOL_DEFINITIONS`:

| Tool | Purpose | Read/Write |
|---|---|---|
| `fhir_read` | Query FHIR resources (Patient, Condition, Observation, etc.) | Read |
| `fhir_write` | Create/update FHIR resources — **requires approved manifest item** | Write |
| `openemr_api` | Arbitrary REST API calls (billing, scheduling) | Read/Write |
| `get_page_context` | Retrieve current UI context (patient, encounter, page type) | Read |
| `submit_manifest` | Submit a change manifest for clinician review | Control-flow |

### Tool dispatch — inline switch statement

The `_execute_tool` method (loop.py:155-251) uses an if/elif chain, NOT the `ToolRegistry`. This is the primary dispatch path:

```python
if tool_call.name == "fhir_read":
    ...
elif tool_call.name == "fhir_write":
    ...
elif tool_call.name == "submit_manifest":
    ...
else:
    return ToolResult(..., is_error=True)  # unknown tool
```

The `ToolRegistry` in `src/tools/registry.py` is instantiated at startup (api/main.py:41-42) and passed as `tools_registry` but is **not used for dispatch** inside the loop. It's wired up but essentially dormant — possibly intended for future extensibility or an earlier architecture.

---

## 6. The `submit_manifest` Tool — Phase Transition Trigger

```python
# loop.py:219-236
elif tool_call.name == "submit_manifest":
    manifest = self._build_manifest(tool_call.arguments, session)
    session.manifest = manifest
    session.phase = "reviewing"
    return ToolResult(
        tool_call_id=tool_call.id,
        content=json.dumps({
            "status": "manifest_submitted",
            "manifest_id": manifest.id,
            "item_count": len(manifest.items),
            "message": "Change manifest submitted for clinician review. Awaiting approval.",
        }),
    )
```

After this tool returns, the loop hits:
```python
if session.phase == "reviewing":
    break
```
...and the turn ends. The clinician must approve/reject items via the `/api/manifest/{session_id}/approve` endpoint before execution.

---

## 7. Write Safety — The Manifest Gate

`fhir_write` requires a `manifest_item_id` that has status `"approved"`:

```python
# loop.py:170-182
elif tool_call.name == "fhir_write":
    manifest_item_id = tool_call.arguments.get("manifest_item_id")
    if not self._is_item_approved(session, manifest_item_id):
        return ToolResult(
            tool_call_id=tool_call.id,
            content="Error: manifest item is not approved. Writes require clinician approval first.",
            is_error=True,
        )
```

This is a **hard programmatic gate** — even if Claude tries to write without approval, the code blocks it.

---

## 8. `execute_approved()` — The Execution Phase

```python
# loop.py:253-312
async def execute_approved(self, session: AgentSession) -> AgentSession:
```

Called from `POST /api/manifest/{session_id}/execute` (api/main.py:160-179). This is a **separate entry point**, not part of the main `run()` loop.

Key mechanics:
- Sets phase to `"executing"`, manifest status to `"executing"`
- **Topological sort** on manifest items respecting `depends_on` ordering (loop.py:456-476)
- Iterates sorted items, executes only those with status `"approved"`
- Uses `fhir_write` for create/update, `api_request` for DELETE
- On any failure: marks item `"failed"`, sets manifest `"failed"`, sets phase `"complete"`, returns immediately (no partial rollback)
- On success: marks all `"completed"`, sets phase `"complete"`, appends a summary assistant message

### Topological sort detail:

```python
def _topological_sort(self, items: list[ManifestItem]) -> list[ManifestItem]:
    """Sort manifest items respecting depends_on ordering."""
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

**Edge case:** No cycle detection. A circular `depends_on` chain would be silently broken by the `visited` set, but the ordering would be arbitrary among cycle members.

---

## 9. Message Format — Anthropic API Mapping

`_build_messages()` (loop.py:314-354) converts the internal `AgentMessage` list to Anthropic's format:

- `role="user"` → `{"role": "user", "content": text}`
- `role="assistant"` → `{"role": "assistant", "content": [text_block, ...tool_use_blocks]}`
- `role="tool"` → `{"role": "user", "content": [tool_result_blocks]}` ← **Note: tool results are sent as `role: "user"`** per Anthropic's API convention.

---

## 10. System Prompt Dynamism

`_get_system_prompt()` (loop.py:356-378) augments `SYSTEM_PROMPT` with:
- **Page context** (patient ID, encounter ID, page type) when available
- **Active manifest info** when phase is `"reviewing"` — tells Claude to wait for approval

This context injection is done on every LLM call, so it reflects the latest session state.

---

## 11. Suggested Running Example — Maria Santos's HbA1c Query

**Scenario:** Dr. Chen asks "What is Maria Santos's latest HbA1c result?"

### Expected round sequence:

**Round 1:**
- LLM receives: system prompt + user message
- LLM calls: `fhir_read(resource_type="Patient", params={"name": "Santos"})`
- Agent executes: HTTP GET to OpenEMR FHIR endpoint
- Result: Patient bundle with Maria Santos, ID = "patient-42"

**Round 2:**
- LLM receives: full conversation including Patient result
- LLM calls: `fhir_read(resource_type="Observation", params={"patient": "patient-42", "code": "4548-4"})` (LOINC for HbA1c)
- Agent executes: HTTP GET for Observations
- Result: Observation bundle with HbA1c = 7.2%, dated 2025-11-15

**Round 3:**
- LLM receives: full conversation including Observation result
- LLM returns: text-only response (no tool calls) — "Maria Santos's most recent HbA1c was 7.2%, recorded on November 15, 2025..."
- Loop breaks (no tool calls → line 91-95)

**Key points for the narrative:**
- This is a **read-only query** — no manifest is ever submitted
- Phase stays `"planning"` throughout (never transitions)
- 3 rounds used out of 15 maximum
- Each round is one full LLM API call

### If the query were "Update Maria's diagnosis to include uncontrolled diabetes":

The sequence would extend:
- Rounds 1-2: same reads
- Round 3: additional `fhir_read` for existing Conditions
- Round 4: LLM calls `submit_manifest` with a CREATE Condition item (E11.65 — Type 2 diabetes with hyperglycemia)
- Phase → `"reviewing"`, loop breaks
- **Out-of-band:** Clinician reviews, approves via API
- `execute_approved()` runs the write
- Phase → `"executing"` → `"complete"`

---

## 12. The HTTP Entry Point

```python
# src/api/main.py:100-126
@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    session = _get_or_create_session(req.session_id)
    # ... set page_context ...
    agent_loop: AgentLoop = app.state.agent_loop
    session = await agent_loop.run(session, req.message)
    _sessions[session.id] = session
    # extract last assistant message
    return ChatResponse(session_id=..., response=..., manifest=..., phase=...)
```

- Sessions stored in-memory (`_sessions` dict) — no persistence
- Each `/api/chat` call runs the full loop synchronously (blocking the request until the loop completes)
- The response extracts the last assistant message from `session.messages`

---

## 13. Edge Cases & Surprising Behavior

### 13a. Phase doesn't reset between turns
If a user starts a new conversation after a completed manifest, the session phase stays `"complete"`. There's no reset logic. A new `AgentSession` must be created for a fresh phase.

### 13b. Tool results masquerade as user messages
In the Anthropic API format, tool results are sent with `role: "user"`. This means the conversation alternates user/assistant/user/assistant, where some "user" messages are actually tool results. The LLM understands this via the `tool_result` content block type.

### 13c. No parallel tool execution
Even when Claude returns multiple tool_use blocks, they execute sequentially. This is a deliberate simplicity choice but means a round with 3 FHIR reads takes 3x the latency.

### 13d. Error recovery is per-tool, not per-round
If one tool in a batch fails, others still execute. The error result is returned to Claude, who can reason about it and retry or adjust.

### 13e. The ToolRegistry exists but isn't used for dispatch
The `ToolRegistry` (registry.py) is instantiated, wired with `register_default_tools()`, and passed to `AgentLoop.__init__()`, but `_execute_tool()` uses a hardcoded if/elif chain. The registry's `execute()` method is never called from the loop.

### 13f. `target_resource_id` field is missing from `ManifestItem`
The `check_conflict()` verification function accesses `item.target_resource_id`, but `ManifestItem` doesn't define this field. Tests work around it with `object.__setattr__()`. This is a known gap (see test_verification.py:286-294, 308).

### 13g. The topological sort doesn't detect cycles
`_topological_sort` uses a `visited` set which prevents infinite loops but doesn't raise on cycles. Circular dependencies are silently resolved with arbitrary ordering.

### 13h. No streaming
The loop waits for complete LLM responses (`messages.create`, not streaming). Each round is blocking. For long multi-round conversations, the HTTP request could take tens of seconds.

### 13i. DELETE uses `api_request`, not `fhir_write`
In `execute_approved()`, DELETE actions go through `openemr_client.api_request()` while create/update go through `fhir_write()`. This means DELETEs bypass the FHIR endpoint and hit the REST API directly.

---

## 14. Verification Layer (Post-Manifest, Pre-Execute)

Triggered from `POST /api/manifest/{session_id}/approve` (api/main.py:129-157), not from within the loop.

Four checks run per manifest item:
1. **Grounding** — Does `source_reference` (e.g., "Encounter/5") actually exist in the EMR?
2. **Constraints** — ICD-10 format for Conditions, CPT format for Procedures, SOAP sections for documents
3. **Confidence** — Scans description and proposed_value for hedging phrases ("possibly", "might be", "unclear", etc.)
4. **Conflict** — Re-reads the target resource to check for concurrent modification

Results are returned to the frontend; they don't block execution (that's a separate `/execute` call).

---

## 15. Architecture Diagram Notes

The flow is:

```
Browser → POST /api/chat
  → _get_or_create_session()
  → AgentLoop.run(session, message)
    → for round in range(15):
        → _call_llm(session) → Anthropic API
        → _extract_tool_calls(response)
        → if no tools: append text, break
        → for each tool: _execute_tool() → OpenEMR FHIR/REST
        → if phase == "reviewing": break
    → else: max-rounds message
  → return ChatResponse

Browser → POST /api/manifest/{id}/approve
  → verify_manifest() → grounding, constraints, confidence, conflict
  → return ApprovalResponse

Browser → POST /api/manifest/{id}/execute
  → AgentLoop.execute_approved(session)
    → topological_sort(items)
    → for approved item: fhir_write or api_request
    → phase → "complete"
```

---

## 16. Important Constants

| Constant | Value | Location |
|---|---|---|
| `MAX_TOOL_ROUNDS` | 15 | `loop.py:22` |
| `MODEL` | `"claude-sonnet-4-20250514"` | `loop.py:23` |
| `max_tokens` | 4096 | `loop.py:141` |
| HTTP timeout (OpenEMR) | 30s | `openemr_client.py:14` |
| Token refresh buffer | 30s | `openemr_client.py:42` |

---

## 17. Dual Dispatch Architecture Note

There are two tool dispatch mechanisms in the codebase:

1. **`AgentLoop._execute_tool()`** (loop.py:155-251) — Hardcoded if/elif chain. This is what actually runs. It directly calls `self.openemr_client` methods.

2. **`ToolRegistry.execute()`** (registry.py:83-92) — Dynamic dispatch via registered callables. Instantiated and wired but never called from the loop.

Both define the same 5 tools with slightly different schemas (the registry's `submit_manifest` takes a nested `{"manifest": {...}}` while the loop's version expects flat `{"patient_id": ..., "items": [...]}`).

This duality suggests an architectural evolution — the registry may have been the original design, later replaced by inline dispatch for tighter control over the manifest/phase workflow.
