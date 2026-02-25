# §1 Research Notes — The Conversation Session

## 1. Primary Source Files

| File | Role |
|------|------|
| `src/agent/models.py` | Pydantic model definitions: `AgentSession`, `AgentMessage`, `PageContext`, `ChangeManifest`, etc. |
| `src/api/main.py` | FastAPI routes + in-memory session store (`_sessions` dict) + session lookup helpers |
| `src/api/schemas.py` | Request/response DTOs (`ChatRequest`, `ChatResponse`, etc.) |
| `src/agent/loop.py` | `AgentLoop` — the core run loop that mutates the session through LLM rounds |
| `tests/unit/test_models.py` | Unit tests covering defaults, phase transitions, serialization |
| `tests/conftest.py` | Shared pytest fixtures (sample manifest items, page contexts) |

---

## 2. The AgentSession Model

**Source:** `src/agent/models.py:65-70`

```python
class AgentSession(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    messages: list[AgentMessage] = Field(default_factory=list)
    manifest: ChangeManifest | None = None
    page_context: PageContext | None = None
    phase: str = "planning"
```

### Fields

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `id` | `str` | `str(uuid4())` | Unique session key, auto-generated. String-coerced UUID4. |
| `messages` | `list[AgentMessage]` | `[]` | Full, append-only conversation history — user turns, assistant turns (with optional tool calls), and tool result turns. |
| `manifest` | `ChangeManifest \| None` | `None` | The active change manifest. Populated when the LLM calls `submit_manifest`. Remains attached for approval/execution lifecycle. |
| `page_context` | `PageContext \| None` | `None` | EHR UI context (patient ID, encounter ID, page type, active form). Set from the client's `ChatRequest.page_context` on each message. |
| `phase` | `str` | `"planning"` | Current lifecycle phase. Free-form string — NOT an enum. Valid values established by convention: `"planning"`, `"reviewing"`, `"executing"`, `"complete"`. |

### Key Design Decisions

1. **UUID as string, not `uuid.UUID`**: The `id` field is `str`, not a native UUID. The factory calls `str(uuid4())`, so the value is a hyphenated UUID string like `"a38f981d-52da-47b1-818c-fbaa9ab56e0c"`. This avoids JSON serialization headaches with Pydantic — no custom serializer needed.

2. **Phase is a bare string, not an Enum**: Unlike `ManifestAction` (which is `str, Enum`), `phase` is just `str`. This means there is no compile-time or runtime validation that a phase value is legal. The only constraint is convention in `loop.py`. Worth noting as a documentation point.

3. **Pydantic BaseModel (not dataclass)**: All models inherit from `pydantic.BaseModel`, giving them `.model_dump()`, `.model_dump_json()`, `.model_validate()`, and validation on construction.

---

## 3. Supporting Models

### AgentMessage (`models.py:58-62`)

```python
class AgentMessage(BaseModel):
    role: str                                    # "user", "assistant", "tool"
    content: str
    tool_calls: list[ToolCall] | None = None     # present on assistant messages with tool use
    tool_results: list[ToolResult] | None = None # present on tool-result messages
```

Three distinct message shapes coexist in one type:
- **User turn**: `role="user"`, `content` = the user text, both optional fields `None`.
- **Assistant turn (text only)**: `role="assistant"`, `content` = reply text, `tool_calls=None`.
- **Assistant turn (tool use)**: `role="assistant"`, `content` may have text, `tool_calls` populated.
- **Tool result turn**: `role="tool"`, `content=""` (empty string!), `tool_results` populated.

Note: Tool result messages always have `content=""` — the actual data is carried in `tool_results`. See `loop.py:110-113`.

### PageContext (`models.py:51-56`)

```python
class PageContext(BaseModel):
    patient_id: str | None = None
    encounter_id: str | None = None
    page_type: str | None = None
    active_form: dict[str, Any] | None = None
```

All fields optional. Represents "what the clinician is looking at in the EHR UI". Set from the API request, then injected into the system prompt by `_get_system_prompt()`.

### ChangeManifest (`models.py:42-48`)

```python
class ChangeManifest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    patient_id: str
    encounter_id: str | None = None
    items: list[ManifestItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = "draft"   # "draft" → "executing" → "completed" / "failed"
```

Also UUID-keyed. The manifest lifecycle tracks separately from the session phase. A session can have at most one manifest at a time.

---

## 4. UUID Keying

Both `AgentSession` and `ChangeManifest` (and `ManifestItem`) use `str(uuid4())` as their default ID factory.

```python
# All three use the same pattern:
id: str = Field(default_factory=lambda: str(uuid4()))
```

The IDs are used as:
- **Session ID**: Dict key in `_sessions`, URL path parameter in manifest endpoints (`/api/manifest/{session_id}/approve`), returned in every `ChatResponse`.
- **Manifest ID**: Returned in `submit_manifest` tool result, used in `ApprovalResponse`.
- **ManifestItem ID**: Used in approval request (`approved_items`/`rejected_items` lists), `depends_on` references for topological ordering.

**Suggested example**: Show a `ChatResponse` JSON illustrating the UUID keying:
```json
{
  "session_id": "a38f981d-52da-47b1-818c-fbaa9ab56e0c",
  "response": "I found the patient record. Let me review...",
  "manifest": null,
  "phase": "planning"
}
```

---

## 5. In-Memory Storage Semantics

**Source:** `src/api/main.py:29`

```python
_sessions: dict[str, AgentSession] = {}
```

### Storage is a module-level global dictionary.

**Implications:**
1. **No persistence** — sessions exist only as long as the process lives. Server restart = all sessions lost.
2. **No eviction** — sessions accumulate indefinitely. There is no TTL, no max-size, no cleanup logic anywhere in the codebase. A long-running server will grow memory linearly with sessions (and each session grows linearly with conversation length).
3. **No concurrency control** — the dict is a plain Python dict. FastAPI runs on asyncio (single-threaded event loop), so there are no data races in typical use, but there are no guards against concurrent requests to the same session interleaving their `run()` calls. If two `/api/chat` requests hit the same session simultaneously, both would append to `messages` and potentially corrupt the conversation history.
4. **Mutation semantics** — `_get_or_create_session` returns a direct reference to the stored object. The `AgentLoop.run()` method mutates the session in-place (appending messages, changing phase, setting manifest). Line 113 (`_sessions[session.id] = session`) re-assigns the key, but this is redundant since the object was already in the dict by reference. The re-assignment exists defensively — it would matter only if `run()` returned a *new* session object, which currently it does not.

### Session Lookup Helpers

```python
# main.py:80-85 — Create-on-first-access pattern
def _get_or_create_session(session_id: str | None) -> AgentSession:
    if session_id and session_id in _sessions:
        return _sessions[session_id]
    session = AgentSession()          # new UUID auto-generated
    _sessions[session.id] = session
    return session

# main.py:88-92 — Strict lookup (404 on miss)
def _get_session(session_id: str) -> AgentSession:
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session
```

**Key pattern**: `/api/chat` uses `_get_or_create_session` (tolerates `None` session_id, creates new sessions). All other endpoints (`/api/manifest/{session_id}/approve`, `/execute`, `GET /manifest`) use `_get_session` (strict, returns 404).

**Subtle behavior**: If a client sends `session_id: "nonexistent-id"` to `/api/chat`, it silently creates a *new* session (ignoring the provided ID). The returned `session_id` will be a fresh UUID. This could surprise clients expecting an error.

### Session List Endpoint

```python
# main.py:191-196
@app.get("/api/sessions")
async def list_sessions() -> list[dict[str, Any]]:
    return [
        {"session_id": s.id, "phase": s.phase, "message_count": len(s.messages)}
        for s in _sessions.values()
    ]
```

Exposes all active sessions with summary data — no auth, no pagination, no filtering.

---

## 6. Phase State Machine

The `phase` field tracks the session's lifecycle stage. Transitions are driven by `AgentLoop`:

```
planning ──[submit_manifest tool]──▶ reviewing ──[execute_approved()]──▶ executing ──▶ complete
                                                                              │
                                                                         (on failure)
                                                                              │
                                                                              ▼
                                                                           complete
```

### Transition Points in Code

| From | To | Trigger | Location |
|------|----|---------|----------|
| `planning` | `reviewing` | LLM calls `submit_manifest` tool | `loop.py:222` |
| `reviewing` | `executing` | `execute_approved()` called | `loop.py:261` |
| `executing` | `complete` | All items succeed | `loop.py:299` |
| `executing` | `complete` | Any item fails | `loop.py:295` |

### Phase-Aware Behaviors

- **Loop break on `reviewing`**: When `submit_manifest` sets phase to `reviewing`, the tool loop breaks immediately (`loop.py:116-117`). This halts the agent before it can call `fhir_write` — the human-in-the-loop gate.
- **System prompt augmentation**: When phase is `reviewing` AND a manifest exists, extra context is injected into the system prompt telling the LLM to wait for approval (`loop.py:370-377`).
- **No reverse transitions**: There is no code path that transitions from `complete` back to `planning`. A session that reaches `complete` is effectively frozen (though nothing enforces this — the model could theoretically mutate `phase` back).

---

## 7. Message History Accumulation

The `messages` list is **append-only** within a session. Each call to `AgentLoop.run()` adds:

1. One `user` message (always — `loop.py:81-83`)
2. Per LLM round (up to `MAX_TOOL_ROUNDS = 15`):
   - One `assistant` message (text and/or tool calls)
   - If tool calls present: one `tool` message containing all results
3. If loop exhausts rounds: a final `assistant` message saying "I've reached the maximum number of tool calls" (`loop.py:119-128`)

The entire history is serialized and sent to the LLM on every call via `_build_messages()` (`loop.py:314-354`). This means:
- **Context window pressure**: Long sessions with many tool calls will fill the context window. There is no summarization, truncation, or windowing.
- **Tool results ≈ user role**: In Anthropic's API format, tool results are sent as `role: "user"` messages with `tool_result` content blocks (`loop.py:350-352`). This is a requirement of the Anthropic API — tool results must be in user messages.

**Suggested example for the text**: Show how 3 messages accumulate:
```python
session = AgentSession()
# After one user message + one assistant text reply:
# session.messages == [
#   AgentMessage(role="user", content="What allergies does this patient have?"),
#   AgentMessage(role="assistant", content="Let me check the patient's allergy list.",
#                tool_calls=[ToolCall(name="fhir_read", ...)]),
#   AgentMessage(role="tool", content="", tool_results=[ToolResult(...)]),
#   AgentMessage(role="assistant", content="The patient has an allergy to Penicillin.")
# ]
```

---

## 8. Page Context Lifecycle

Page context is set per-request, not once:

```python
# main.py:104-109
if req.page_context:
    session.page_context = PageContext(
        patient_id=req.page_context.patient_id,
        encounter_id=req.page_context.encounter_id,
        page_type=req.page_context.page_type,
    )
```

Each `/api/chat` call can update the page context. Note: `active_form` from `PageContext` is never populated by the API — the `PageContextRequest` schema (`schemas.py:8-11`) doesn't include `active_form`. It exists only in the model definition, not in the API surface.

---

## 9. Edge Cases & Surprising Behaviors

1. **Silent session creation on bad ID**: `_get_or_create_session("nonexistent")` creates a new session rather than returning 404. Only manifest/approval endpoints 404 on unknown sessions.

2. **Phase is unenforced**: Any string is valid for `phase`. `session.phase = "bananas"` passes silently. The test at `test_models.py:105-113` demonstrates free mutation without validation.

3. **Redundant re-store**: Lines `main.py:113` and `main.py:169` re-assign `_sessions[session.id] = session` after `run()`/`execute_approved()`, but the session object is the same reference already in the dict. This is defensive/no-op code.

4. **No session isolation**: The `_sessions` dict is shared across all users/requests. There's no user-scoping or authentication. Any caller who knows (or guesses) a session UUID can interact with any session.

5. **`active_form` phantom field**: `PageContext.active_form` exists in the model but is never populated through the API. It's effectively dead code unless set programmatically.

6. **`MAX_TOOL_ROUNDS = 15` (loop.py) vs test expectations**: The test file (`test_models.py`) doesn't test the loop constant. The AGENTS.md notes a previous mismatch (10 vs 40) in `agent.test.js` (collabboard project), suggesting this value has been a source of confusion.

7. **Manifest replaceability**: If the LLM calls `submit_manifest` twice in the same session, the second manifest silently replaces the first (`session.manifest = manifest` at `loop.py:221`). No warning, no merge.

8. **No message deletion or editing**: Once appended to `session.messages`, messages cannot be removed or modified through the API. The list only grows.

9. **`datetime.utcnow()` deprecation**: `ChangeManifest.created_at` uses `datetime.utcnow()` which is deprecated in Python 3.12+ in favor of `datetime.now(UTC)`.

---

## 10. Test Coverage for AgentSession

**Source:** `tests/unit/test_models.py:96-113`

```python
class TestAgentSession:
    def test_defaults(self):
        session = AgentSession()
        assert session.id             # truthy (non-empty string)
        assert session.messages == []
        assert session.manifest is None
        assert session.page_context is None
        assert session.phase == "planning"

    def test_phase_transitions(self):
        session = AgentSession()
        assert session.phase == "planning"
        session.phase = "executing"
        assert session.phase == "executing"
        session.phase = "reviewing"
        assert session.phase == "reviewing"
        session.phase = "complete"
        assert session.phase == "complete"
```

The tests verify:
- Default values are correct
- Phase can be freely mutated (no state machine enforcement)
- Each `AgentSession()` gets a unique, truthy ID

Note: Tests do NOT verify UUID uniqueness across instances. They also don't test message accumulation or manifest attachment — those are integration concerns tested elsewhere (via loop tests).

---

## 11. Suggested Diagrams

### Session Object Graph
```
AgentSession
├── id: "a38f981d-..."
├── phase: "planning"
├── page_context: PageContext?
│   ├── patient_id
│   ├── encounter_id
│   └── page_type
├── manifest: ChangeManifest?
│   ├── id: "b7c2e..."
│   ├── patient_id
│   ├── items: [ManifestItem, ...]
│   │   ├── id: "c9d3f..."
│   │   ├── resource_type, action, proposed_value
│   │   └── depends_on: [item_id, ...]
│   └── status: "draft"
└── messages: [AgentMessage, ...]
    ├── {role: "user", content: "..."}
    ├── {role: "assistant", content: "...", tool_calls: [...]}
    ├── {role: "tool", content: "", tool_results: [...]}
    └── {role: "assistant", content: "..."}
```

### Storage Architecture
```
Module-level global:  _sessions: dict[str, AgentSession]

  ┌──────────────────────────────────────────────┐
  │  "a38f..."  →  AgentSession(phase="planning")│
  │  "b7c2..."  →  AgentSession(phase="complete")│
  │  "c9d3..."  →  AgentSession(phase="reviewing")│
  └──────────────────────────────────────────────┘
       ▲                    ▲
       │                    │
  _get_or_create       _get_session
  (chat endpoint)     (manifest endpoints)
```
