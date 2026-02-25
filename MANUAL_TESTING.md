# Manual Testing Guide: EMR Agent Features

This guide walks you through **each feature** we've added to the OpenEMR agent, with step-by-step instructions to verify them manually via HTTP requests.

---

## Prerequisites

**All services must be running:**
```bash
# Terminal 1: Start Docker services
sudo systemctl start docker
cd /home/login/PycharmProjects/gauntlet/week2_emr_agent
docker compose up -d

# Wait ~3-4 min for OpenEMR to initialize, then check:
curl -s http://localhost:80/apis/default/fhir/metadata | head -c 100

# Terminal 2: Start the agent API
source .venv/bin/activate
uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

**Verify services are healthy:**
```bash
curl http://localhost:8000/api/health
# → {"status":"healthy","openemr_connected":true}
```

---

## Feature 1: Health Check & Service Status

**What it does:** Verify that the agent backend and OpenEMR are connected and responding.

**Endpoint:** `GET /api/health`

**Test:**
```bash
curl -X GET http://localhost:8000/api/health
```

**Expected response:**
```json
{
  "status": "healthy",
  "openemr_connected": true,
  "openemr_status": "ok"
}
```

**Manual verification:**
- ✅ `status` is `"healthy"`
- ✅ `openemr_connected` is `true`
- ✅ `openemr_status` is `"ok"` (not `"error"` or `"starting"`)

---

## Feature 2: FHIR Metadata Retrieval

**What it does:** Inspect OpenEMR's FHIR capability statement to see what resources are available.

**Endpoint:** `GET /api/fhir/metadata`

**Test:**
```bash
curl -X GET http://localhost:8000/api/fhir/metadata | jq '.resourceType, .rest[0].resource[] | select(.type == "Patient") | .type'
```

**Expected response:**
```json
"CapabilityStatement"
```

**Manual verification:**
- ✅ Response contains `"resourceType": "CapabilityStatement"`
- ✅ `rest[0].resource[]` includes types like `"Patient"`, `"Condition"`, `"Observation"`, etc.

---

## Feature 3: Session Management

### 3a. Create a New Session

**What it does:** Start a new conversation session between a clinician and the agent. Each session tracks messages, manifests, and state.

**Endpoint:** `POST /api/sessions`

**Test:**
```bash
curl -X POST http://localhost:8000/api/sessions \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json"
```

**Expected response:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "phase": "planning"
}
```

**Manual verification:**
- ✅ `session_id` is a valid UUID
- ✅ `phase` starts at `"planning"`
- ✅ Save this `session_id` for subsequent tests

### 3b. List All Sessions

**What it does:** Get a summary of all active sessions for the logged-in clinician.

**Endpoint:** `GET /api/sessions`

**Test:**
```bash
curl -X GET http://localhost:8000/api/sessions \
  -H "openemr_user_id: admin"
```

**Expected response:**
```json
[
  {
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "phase": "planning",
    "message_count": 2,
    "created_at": "2025-02-24T10:30:00",
    "first_message_preview": "What are Maria's active conditions?"
  }
]
```

**Manual verification:**
- ✅ Response is a list of sessions
- ✅ Each session has `session_id`, `phase`, `message_count`, `created_at`, `first_message_preview`
- ✅ Sessions belong to the requesting user (`openemr_user_id`)

---

## Feature 4: Chat & Agent Reasoning

**What it does:** Send a natural-language message to the agent. Claude reads patient data via FHIR and reasons about the task, proposing changes through the manifest system.

**Endpoint:** `POST /api/chat`

### Setup: Get a Patient ID

First, get a real patient from OpenEMR to chat about:
```bash
curl -X GET "http://localhost:80/apis/default/fhir/Patient?_count=1" \
  -H "Authorization: Bearer $(curl -s -X POST http://localhost:80/oauth2/default/token \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "grant_type=password&username=admin&password=pass&client_id=$(grep OPENEMR_CLIENT_ID .env | cut -d= -f2)&client_secret=$(grep OPENEMR_CLIENT_SECRET .env | cut -d= -f2)" \
    | jq -r .access_token)" \
  | jq '.entry[0].resource.id'
```

Or use a seed patient from the test data (default IDs are often `1`, `2`, or `3`).

### 4a. Simple Read Query (No Writes)

**Test:** Ask the agent a question about a patient's conditions.

```bash
SESSION_ID=$(curl -s -X POST http://localhost:8000/api/sessions \
  -H "openemr_user_id: admin" | jq -r .session_id)

curl -X POST http://localhost:8000/api/chat \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json" \
  -d "{
    \"session_id\": \"$SESSION_ID\",
    \"message\": \"What are the active diagnoses for patient 1?\",
    \"page_context\": {
      \"patient_id\": \"1\",
      \"page_type\": \"problem_list\"
    }
  }" | jq .
```

**Expected response structure:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "response": "Patient 1 (Maria Santos) has the following active diagnoses: ...",
  "manifest": null,
  "phase": "planning",
  "tool_calls_summary": [
    {"id": "toolu_01...", "name": "fhir_read"}
  ]
}
```

**Manual verification:**
- ✅ `response` contains clinical information (diagnoses, medications, etc.)
- ✅ `manifest` is `null` (no writes proposed for read-only queries)
- ✅ `tool_calls_summary` shows the agent used `fhir_read`
- ✅ `phase` is still `"planning"` (no manifest yet)

### 4b. Write Request (Generates Manifest)

**Test:** Ask the agent to propose a clinical action that requires a write.

```bash
SESSION_ID=$(curl -s -X POST http://localhost:8000/api/sessions \
  -H "openemr_user_id: admin" | jq -r .session_id)

curl -X POST http://localhost:8000/api/chat \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json" \
  -d "{
    \"session_id\": \"$SESSION_ID\",
    \"message\": \"Maria Santos has just been diagnosed with hypertension. Add this to her active problems.\",
    \"page_context\": {
      \"patient_id\": \"1\",
      \"page_type\": \"problem_list\"
    }
  }" | jq .
```

**Expected response structure:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "response": "I'm proposing to add hypertension (I10) to Maria's active diagnoses. Please review the manifest below.",
  "manifest": {
    "id": "manifest-uuid",
    "patient_id": "1",
    "status": "draft",
    "items": [
      {
        "id": "item-uuid",
        "resource_type": "Condition",
        "action": "create",
        "proposed_value": {
          "resourceType": "Condition",
          "code": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": "I10"}]},
          "subject": {"reference": "Patient/1"}
        },
        "source_reference": "Clinician request",
        "description": "Add hypertension to active problems",
        "confidence": "high",
        "status": "pending"
      }
    ]
  },
  "phase": "planning",
  "tool_calls_summary": [
    {"id": "toolu_01...", "name": "fhir_read"},
    {"id": "toolu_02...", "name": "submit_manifest"}
  ]
}
```

**Manual verification:**
- ✅ `manifest` is **not** `null` (write proposed)
- ✅ `manifest.items` is a non-empty list
- ✅ Each item has: `id`, `resource_type`, `action`, `proposed_value`, `source_reference`, `description`, `confidence`, `status`
- ✅ Item `status` is `"pending"` (not yet approved)
- ✅ `phase` is still `"planning"` (awaiting approval)

---

## Feature 5: View Manifest

**What it does:** Retrieve the current manifest for a session (used between agent rounds or before approval).

**Endpoint:** `GET /api/manifest/{session_id}`

**Test:**
```bash
curl -X GET "http://localhost:8000/api/manifest/$SESSION_ID" \
  -H "openemr_user_id: admin" | jq .
```

**Expected response:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "manifest": {
    "id": "manifest-uuid",
    "patient_id": "1",
    "encounter_id": null,
    "status": "draft",
    "items": [
      {
        "id": "item-uuid",
        "resource_type": "Condition",
        "action": "create",
        "proposed_value": {...},
        "current_value": null,
        "source_reference": "Clinician request",
        "description": "Add hypertension to active problems",
        "confidence": "high",
        "status": "pending",
        "target_resource_id": null,
        "depends_on": [],
        "execution_result": null
      }
    ],
    "created_at": "2025-02-24T10:35:00"
  }
}
```

**Manual verification:**
- ✅ `manifest.status` is `"draft"` or `"in_review"`
- ✅ Items show detailed proposed changes
- ✅ Each item has all required fields

---

## Feature 6: Approve/Reject Manifest Items

**What it does:** Clinician reviews proposed changes and approves or rejects individual items. Approval triggers verification checks.

**Endpoint:** `POST /api/manifest/{session_id}/approve`

### 6a. Approve an Item

**Test:**
```bash
# Use the item ID from the manifest retrieved in Feature 5
ITEM_ID="item-uuid"

curl -X POST "http://localhost:8000/api/manifest/$SESSION_ID/approve" \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json" \
  -d "{
    \"approved_items\": [\"$ITEM_ID\"],
    \"rejected_items\": [],
    \"modified_items\": []
  }" | jq .
```

**Expected response:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "manifest_id": "manifest-uuid",
  "passed": true,
  "results": [
    {
      "check_name": "grounding_check",
      "passed": true,
      "details": "Item cites valid FHIR resource"
    },
    {
      "check_name": "code_validity_check",
      "passed": true,
      "details": "I10 is a valid ICD-10 code"
    },
    {
      "check_name": "confidence_check",
      "passed": true,
      "details": "Confidence >= 0.7"
    }
  ]
}
```

**Manual verification:**
- ✅ `passed` is `true` (all verification checks passed)
- ✅ `results` is a list of verification check outcomes
- ✅ Each result has: `check_name`, `passed`, `details`
- ✅ Item status changed to `"approved"` in the manifest

### 6b. Reject an Item

**Test:**
```bash
curl -X POST "http://localhost:8000/api/manifest/$SESSION_ID/approve" \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json" \
  -d "{
    \"approved_items\": [],
    \"rejected_items\": [\"$ITEM_ID\"],
    \"modified_items\": []
  }" | jq .
```

**Expected response:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "manifest_id": "manifest-uuid",
  "passed": true,
  "results": []
}
```

**Manual verification:**
- ✅ Item status changed to `"rejected"` in the manifest
- ✅ Rejected items skip verification checks (no verification against rejected items)

### 6c. Modify an Item Before Approval

**Test:** Some items may allow modifications (e.g., changing a proposed value):

```bash
curl -X POST "http://localhost:8000/api/manifest/$SESSION_ID/approve" \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json" \
  -d "{
    \"approved_items\": [\"$ITEM_ID\"],
    \"rejected_items\": [],
    \"modified_items\": [
      {
        \"id\": \"$ITEM_ID\",
        \"proposed_value\": {
          \"resourceType\": \"Condition\",
          \"code\": {\"coding\": [{\"system\": \"http://hl7.org/fhir/sid/icd-10-cm\", \"code\": \"I11\"}]},
          \"subject\": {\"reference\": \"Patient/1\"}
        }
      }
    ]
  }" | jq .
```

**Manual verification:**
- ✅ Item's `proposed_value` was updated
- ✅ Verification ran against the **modified** value

---

## Feature 7: Execute Approved Changes

**What it does:** Write all approved items to OpenEMR via FHIR API. Each item executes sequentially, and the manifest updates with execution results.

**Endpoint:** `POST /api/manifest/{session_id}/execute`

**Prerequisites:**
- At least one item must be approved (see Feature 6a)

**Test:**
```bash
curl -X POST "http://localhost:8000/api/manifest/$SESSION_ID/execute" \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json" | jq .
```

**Expected response:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "phase": "reviewing",
  "manifest_status": "executed",
  "items": [
    {
      "id": "item-uuid",
      "status": "approved",
      "execution_result": "Created Condition resource with ID: condition-uuid"
    }
  ]
}
```

**Manual verification:**
- ✅ `phase` changed from `"planning"` to `"reviewing"` (or similar)
- ✅ `manifest_status` is `"executed"`
- ✅ Each item's `execution_result` contains success details or error message
- ✅ For successful creations, `execution_result` includes the resource ID created in OpenEMR
- ✅ Verify in OpenEMR that the change actually persisted:
  ```bash
  curl -X GET "http://localhost:80/apis/default/fhir/Condition?patient=Patient/1" \
    -H "Authorization: Bearer <token>" | jq '.entry[] | select(.resource.code.coding[0].code == "I10")'
  ```

---

## Feature 8: Message History Retrieval

**What it does:** Get the full conversation history (user/assistant/tool messages) for a session.

**Endpoint:** `GET /api/sessions/{session_id}/messages`

**Test:**
```bash
curl -X GET "http://localhost:8000/api/sessions/$SESSION_ID/messages" \
  -H "openemr_user_id: admin" | jq .
```

**Expected response:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "messages": [
    {
      "role": "user",
      "content": "What are the active diagnoses for patient 1?",
      "tool_calls": null,
      "tool_results": null
    },
    {
      "role": "assistant",
      "content": "Patient 1 has...",
      "tool_calls": [
        {
          "name": "fhir_read",
          "arguments": {
            "resource_type": "Condition",
            "filters": {"patient": "Patient/1"}
          },
          "id": "toolu_01..."
        }
      ],
      "tool_results": null
    },
    {
      "role": "tool",
      "content": "",
      "tool_calls": null,
      "tool_results": [
        {
          "tool_call_id": "toolu_01...",
          "content": "[{\"resourceType\":\"Condition\", ...}]",
          "is_error": false
        }
      ]
    }
  ],
  "manifest": {...}
}
```

**Manual verification:**
- ✅ Messages alternate between `"user"`, `"assistant"`, and `"tool"` roles
- ✅ Assistant messages with tool calls include the `tool_calls` array
- ✅ Tool response messages include `tool_results` array
- ✅ Each tool call has: `name`, `arguments`, `id`
- ✅ Each tool result has: `tool_call_id`, `content`, `is_error`

---

## Feature 9: Verification Checks (Automated Quality Gates)

**What it does:** When items are approved, automatic verification checks run to catch errors before writes:

1. **Grounding Check**: Item cites valid FHIR resource
2. **Code Validity Check**: ICD-10 and CPT codes are real
3. **Confidence Check**: Agent confidence meets threshold (>= 0.7)
4. **Conflict Detection**: No inconsistent updates to same resource

This is run as part of the **Approve** endpoint (Feature 6).

**Test:** Create a manifest with a bad ICD-10 code:

```bash
# Chat request that proposes an invalid code
curl -X POST http://localhost:8000/api/chat \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json" \
  -d "{
    \"session_id\": \"$SESSION_ID\",
    \"message\": \"Add an invalid diagnosis code BADCODE to patient 1.\",
    \"page_context\": {
      \"patient_id\": \"1\",
      \"page_type\": \"problem_list\"
    }
  }" | jq '.manifest.items[0].proposed_value'
```

Then approve the manifest:

```bash
curl -X POST "http://localhost:8000/api/manifest/$SESSION_ID/approve" \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json" \
  -d "{
    \"approved_items\": [\"$ITEM_ID\"],
    \"rejected_items\": [],
    \"modified_items\": []
  }" | jq .results
```

**Expected response:**
```json
[
  {
    "check_name": "code_validity_check",
    "passed": false,
    "details": "BADCODE is not a valid ICD-10 code"
  }
]
```

**Manual verification:**
- ✅ Code validity check **fails** for invalid codes
- ✅ Error message is descriptive
- ✅ Even if `passed=false`, the manifest updates; clinician can modify or reject
- ✅ Execution will be blocked if critical checks fail

---

## Feature 10: Observability & Tracing (Jaeger)

**What it does:** Every API call, tool invocation, and verification check emits OpenTelemetry spans. Traces can be viewed in Jaeger UI.

**Jaeger UI:** http://localhost:16686

**Manual verification:**

1. Open Jaeger UI in a browser: http://localhost:16686
2. Service name dropdown: select `"openemr-agent"`
3. Click **Find Traces**
4. You'll see traces for:
   - `POST /api/chat` (entire conversation turn)
   - `fhir_read` (FHIR tool calls)
   - `submit_manifest` (manifest creation)
   - `verify_manifest` (verification checks)

**Example trace inspection:**
- Click a `/api/chat` trace
- Expand spans to see:
  - **Service name**: `openemr-agent`
  - **Operation**: `/api/chat`
  - **Duration**: Total time for agent reasoning + tool calls
  - **Tags/Attributes**:
    - `http.method`, `http.status_code`
    - `session_id`, `user_id`
    - `tool_name`, `tool_call_count`
    - `token_count` (LLM input/output)
  - **Child spans**: Tool calls, verification checks

**Verify token counting:**
- In a span's attributes, look for `llm.input_tokens` and `llm.output_tokens`
- These should be non-zero and reasonable (e.g., 200-1000 tokens for typical queries)

---

## Feature 11: Authentication & User Isolation

**What it does:** Sessions are isolated per user (identified by OpenEMR user ID header). One user cannot access another's sessions.

**Test:** Create a session as `admin`, then try to access it as a different user:

```bash
# Create session as admin
SESSION_ID=$(curl -s -X POST http://localhost:8000/api/sessions \
  -H "openemr_user_id: admin" | jq -r .session_id)

# Try to access as different user
curl -X GET "http://localhost:8000/api/sessions/$SESSION_ID/messages" \
  -H "openemr_user_id: nurse"
```

**Expected response:**
```json
{"detail": "Forbidden"}
```

**Manual verification:**
- ✅ Different user gets a **403 Forbidden** error
- ✅ Cannot access sessions from other users

**Missing header test:**
```bash
curl -X GET "http://localhost:8000/api/sessions/$SESSION_ID/messages"
# (no openemr_user_id header)
```

**Expected response:**
```json
{"detail": "Authentication required"}
```

**Manual verification:**
- ✅ Missing header returns **401 Unauthorized**

---

## Feature 12: Page Context Integration

**What it does:** The agent reads UI context (current patient, encounter, visible page data) from the request. This informs the agent about what the clinician is currently looking at.

**Test:**
```bash
curl -X POST http://localhost:8000/api/chat \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json" \
  -d "{
    \"session_id\": \"$SESSION_ID\",
    \"message\": \"Summarize what's on this page.\",
    \"page_context\": {
      \"patient_id\": \"1\",
      \"encounter_id\": \"enc-123\",
      \"page_type\": \"encounter_summary\",
      \"visible_data\": {
        \"chief_complaint\": \"Chest pain\",
        \"vital_signs\": {\"bp\": \"150/90\", \"hr\": 88}
      }
    }
  }" | jq .response
```

**Expected response:**
```
"Based on the current page context, I see patient 1 is in encounter enc-123. 
Chief complaint is chest pain, with elevated blood pressure (150/90) and normal heart rate."
```

**Manual verification:**
- ✅ Agent references the `visible_data` in its response
- ✅ Agent knows the current patient and encounter
- ✅ Page context is used to provide contextual guidance

---

## Quick Reference: Full Workflow

Here's a complete end-to-end test of all features:

```bash
#!/bin/bash

BASE_URL="http://localhost:8000"
USER_ID="admin"

echo "1. Health check"
curl -s $BASE_URL/api/health | jq .

echo "2. Create session"
SESSION_ID=$(curl -s -X POST $BASE_URL/api/sessions \
  -H "openemr_user_id: $USER_ID" | jq -r .session_id)
echo "Session ID: $SESSION_ID"

echo "3. Chat: read query"
curl -s -X POST $BASE_URL/api/chat \
  -H "openemr_user_id: $USER_ID" \
  -H "Content-Type: application/json" \
  -d "{
    \"session_id\": \"$SESSION_ID\",
    \"message\": \"What are patient 1's active conditions?\",
    \"page_context\": {\"patient_id\": \"1\", \"page_type\": \"problem_list\"}
  }" | jq '.response, .tool_calls_summary'

echo "4. Chat: write request"
curl -s -X POST $BASE_URL/api/chat \
  -H "openemr_user_id: $USER_ID" \
  -H "Content-Type: application/json" \
  -d "{
    \"session_id\": \"$SESSION_ID\",
    \"message\": \"Add hypertension to the problem list.\",
    \"page_context\": {\"patient_id\": \"1\", \"page_type\": \"problem_list\"}
  }" | jq '.manifest.items[0] | {id, action, status}'

echo "5. Get manifest"
curl -s -X GET $BASE_URL/api/manifest/$SESSION_ID \
  -H "openemr_user_id: $USER_ID" | jq '.manifest.items[0] | {id, action, status}'

echo "6. Approve item"
ITEM_ID=$(curl -s -X GET $BASE_URL/api/manifest/$SESSION_ID \
  -H "openemr_user_id: $USER_ID" | jq -r '.manifest.items[0].id')
curl -s -X POST $BASE_URL/api/manifest/$SESSION_ID/approve \
  -H "openemr_user_id: $USER_ID" \
  -H "Content-Type: application/json" \
  -d "{
    \"approved_items\": [\"$ITEM_ID\"],
    \"rejected_items\": [],
    \"modified_items\": []
  }" | jq '.passed, .results[] | {check_name, passed}'

echo "7. Execute"
curl -s -X POST $BASE_URL/api/manifest/$SESSION_ID/execute \
  -H "openemr_user_id: $USER_ID" | jq '.phase, .manifest_status'

echo "8. View messages"
curl -s -X GET $BASE_URL/api/sessions/$SESSION_ID/messages \
  -H "openemr_user_id: $USER_ID" | jq '.messages | length'

echo "9. List sessions"
curl -s -X GET $BASE_URL/api/sessions \
  -H "openemr_user_id: $USER_ID" | jq '. | length'

echo "✅ All features tested!"
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `openemr_connected: false` | Wait for OpenEMR startup (~3-4 min). Check logs: `docker logs week2_emr_agent-openemr-1` |
| `401 Unauthorized` | Add `-H "openemr_user_id: admin"` header to request |
| `404 Session not found` | Session ID is invalid or belongs to different user |
| `400 No manifest for this session` | Chat request didn't propose changes; approve/execute only work if manifest exists |
| Tool call errors in response | Check OpenEMR FHIR API logs: `docker logs week2_emr_agent-agent-1` |
| Jaeger not showing spans | Ensure jaeger container is running: `docker logs week2_emr_agent-jaeger-1` |

---

## Notes

- **All timestamps** are ISO 8601 format (UTC)
- **Session IDs** and **Manifest IDs** are UUIDs (v4)
- **Item IDs** within manifests are also UUIDs
- **Token counts** in spans are approximate (may vary by model version)
- **Verification checks** are logged in Jaeger with full details
- **Execution results** show the actual resource ID created, or error details if failed
