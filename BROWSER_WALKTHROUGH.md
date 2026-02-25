# Browser Walkthrough: Using the EMR Agent

**How to access and interact with the clinical EMR system and agent through your browser as an end-user.**

---

## Prerequisites

Both services must be running:

```bash
# Terminal 1: Start Docker
sudo systemctl start docker
cd /home/login/PycharmProjects/gauntlet/week2_emr_agent
docker compose up -d

# Wait ~3-4 min for OpenEMR to initialize
sleep 240
curl -s http://localhost:80/apis/default/fhir/metadata | head -c 100

# Terminal 2: Start agent API
source .venv/bin/activate
uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

Verify both are healthy:
```bash
curl http://localhost:8000/api/health
# → {"status":"healthy","openemr_connected":true}
```

---

## Step 1: Access OpenEMR Web UI

1. **Open your browser** to: **http://localhost:80**
   - You'll see the OpenEMR login page

2. **Login with credentials:**
   - **Username:** `admin`
   - **Password:** `pass`

3. **Click "Login"**

---

## Step 2: Navigate to a Patient

After login, you're in the **Dashboard**.

1. **Click "Patients"** in the left sidebar (or search menu)
   
2. **Search for a test patient:**
   - Type `Maria` in the search box
   - You'll see **"Maria Santos"** (one of three seed patients)
   - Click to open her record

3. **You're now viewing Maria's chart**

---

## Step 3: Explore Maria's Clinical Data

You can now browse:

- **Problem List** — Her active diagnoses
  - Type 2 Diabetes (E11.9)
  - Hypertension (I10)

- **Medications** — What she's currently taking
  - Metformin 500mg
  - Lisinopril 10mg

- **Lab Results** — Recent labs
  - HbA1c: 8.2% (elevated)

- **Allergies** — NKDA (No Known Drug Allergies)

- **Encounters** — Past visits

**Note:** Right now, you're just **browsing**. The agent isn't integrated into the OpenEMR UI yet — that's Phase 2. For now, the agent works via **API calls only**.

---

## Step 4: Interact with the Agent (via API)

Since there's no browser UI for the agent yet, you'll use **curl** to chat with it. Open a **third terminal window**:

### 4a. Create a new session

```bash
curl -X POST http://localhost:8000/api/sessions \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json"
```

Save the `session_id` from the response. Let's call it `$SESSION_ID`.

### 4b. Ask the agent about Maria

While you're viewing Maria's chart in OpenEMR (patient_id=1), ask the agent:

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json" \
  -d "{
    \"session_id\": \"$SESSION_ID\",
    \"message\": \"Summarize Maria's current medications and conditions.\",
    \"page_context\": {
      \"patient_id\": \"1\",
      \"page_type\": \"chart_summary\"
    }
  }" | jq '.response'
```

**The agent responds with:**
```
Maria Santos (patient 1) has the following active problems:
- Type 2 Diabetes Mellitus (E11.9)
- Hypertension (I10)

Current medications:
- Metformin 500mg daily
- Lisinopril 10mg daily

Recent lab: HbA1c 8.2% (elevated, targets <7.0%)
```

---

## Step 5: Propose a Clinical Action (Generate a Manifest)

Now ask the agent to **propose a change**:

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json" \
  -d "{
    \"session_id\": \"$SESSION_ID\",
    \"message\": \"Maria's HbA1c is elevated. I should increase her Metformin dose. Draft a note suggesting 1000mg daily instead of 500mg, and update her problem list if needed.\",
    \"page_context\": {
      \"patient_id\": \"1\",
      \"page_type\": \"encounter\"
    }
  }" | jq '.'
```

**The agent responds with:**
- A **manifest** containing proposed changes
- Each change shows what it's doing and why
- **Status: pending** (awaiting your approval)

---

## Step 6: Review the Manifest in Detail

Back in the terminal, view the manifest:

```bash
curl -X GET "http://localhost:8000/api/manifest/$SESSION_ID" \
  -H "openemr_user_id: admin" | jq '.manifest'
```

**You'll see something like:**
```json
{
  "id": "manifest-uuid",
  "patient_id": "1",
  "status": "draft",
  "items": [
    {
      "id": "item-1",
      "resource_type": "Medication",
      "action": "update",
      "description": "Increase Metformin from 500mg to 1000mg daily",
      "proposed_value": {
        "dosage": "1000mg",
        "frequency": "once daily"
      },
      "confidence": "high",
      "status": "pending"
    }
  ]
}
```

---

## Step 7: Approve the Changes

You're satisfied with the proposal. **Approve it:**

```bash
ITEM_ID="item-1"  # From the manifest above

curl -X POST "http://localhost:8000/api/manifest/$SESSION_ID/approve" \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json" \
  -d "{
    \"approved_items\": [\"$ITEM_ID\"],
    \"rejected_items\": [],
    \"modified_items\": []
  }" | jq '.passed, .results'
```

**Verification checks run:**
- ✅ Grounding check (is the change based on real data?)
- ✅ Code validity (is the medication code valid?)
- ✅ Confidence check (agent confidence >= 70%?)
- ✅ Conflict detection (no contradictions?)

If all pass, the item status changes to `"approved"`.

---

## Step 8: Execute the Changes

Now **write the changes to OpenEMR:**

```bash
curl -X POST "http://localhost:8000/api/manifest/$SESSION_ID/execute" \
  -H "openemr_user_id: admin"
```

**Response:**
```json
{
  "phase": "reviewing",
  "manifest_status": "executed",
  "items": [
    {
      "id": "item-1",
      "status": "approved",
      "execution_result": "Updated Medication dosage: Metformin now 1000mg daily"
    }
  ]
}
```

---

## Step 9: Verify in OpenEMR

**Go back to your browser** and **refresh Maria's chart**:

1. Click the **Medications tab**
2. You should see:
   - ✅ Metformin dosage updated to **1000mg daily**
   - Last updated: just now

The agent's changes are now **permanently in the EMR**.

---

## Step 10: Multi-turn Agent Conversation

The agent can reason across multiple turns. Continue the conversation:

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json" \
  -d "{
    \"session_id\": \"$SESSION_ID\",
    \"message\": \"Her HbA1c is still a concern. Should we also check her kidney function before increasing the dose further?\",
    \"page_context\": {
      \"patient_id\": \"1\",
      \"page_type\": \"encounter\"
    }
  }" | jq '.response'
```

The agent maintains context from previous turns and can propose additional actions.

---

## Step 11: View Full Conversation History

To see everything you discussed:

```bash
curl -X GET "http://localhost:8000/api/sessions/$SESSION_ID/messages" \
  -H "openemr_user_id: admin" | jq '.messages[] | {role, content: (.content | .[0:100])}'
```

Shows all user/assistant/tool messages in the session.

---

## Step 12: Check Observability (Jaeger)

Every API call is traced:

1. Open **http://localhost:16686** (Jaeger UI)
2. Service: `openemr-agent`
3. Click **Find Traces**
4. Click on a `/api/chat` trace to see:
   - Full execution timeline
   - Tool calls (fhir_read, submit_manifest, etc.)
   - Token counts
   - Verification checks
   - Latency per operation

---

## Common Workflows

### Workflow 1: Quick Patient Summary
```bash
curl -X POST http://localhost:8000/api/chat \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json" \
  -d "{
    \"session_id\": \"$SESSION_ID\",
    \"message\": \"Give me a 2-sentence summary of this patient's current status.\",
    \"page_context\": {\"patient_id\": \"1\", \"page_type\": \"chart\"}
  }" | jq '.response'
```

### Workflow 2: Clinical Decision Support
```bash
curl -X POST http://localhost:8000/api/chat \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json" \
  -d "{
    \"session_id\": \"$SESSION_ID\",
    \"message\": \"Based on her HbA1c and current medications, what would you recommend?\",
    \"page_context\": {\"patient_id\": \"1\", \"page_type\": \"encounter\"}
  }" | jq '.response'
```

### Workflow 3: Documentation Assistance
```bash
curl -X POST http://localhost:8000/api/chat \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json" \
  -d "{
    \"session_id\": \"$SESSION_ID\",
    \"message\": \"Draft a brief encounter note documenting today's medication adjustment.\",
    \"page_context\": {\"patient_id\": \"1\", \"page_type\": \"encounter\"}
  }" | jq '.response'
```

### Workflow 4: Propose + Review + Execute
```bash
# 1. Chat & get manifest
RESPONSE=$(curl -s -X POST http://localhost:8000/api/chat \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json" \
  -d "{
    \"session_id\": \"$SESSION_ID\",
    \"message\": \"Add a new allergy: penicillin (Type 1 hypersensitivity).\",
    \"page_context\": {\"patient_id\": \"1\"}
  }")

ITEM_ID=$(echo "$RESPONSE" | jq -r '.manifest.items[0].id')

# 2. Approve
curl -s -X POST "http://localhost:8000/api/manifest/$SESSION_ID/approve" \
  -H "openemr_user_id: admin" \
  -H "Content-Type: application/json" \
  -d "{
    \"approved_items\": [\"$ITEM_ID\"],
    \"rejected_items\": [],
    \"modified_items\": []
  }" | jq '.passed'

# 3. Execute
curl -s -X POST "http://localhost:8000/api/manifest/$SESSION_ID/execute" \
  -H "openemr_user_id: admin" | jq '.manifest_status'

# 4. Verify in OpenEMR browser (refresh Maria's chart → Allergies tab)
```

---

## Test Patients

Three seed patients with rich clinical data:

| Patient | Conditions | Medications | Key Lab |
|---------|-----------|------------|---------|
| **Maria Santos** (PID=1) | T2DM, HTN | Metformin 500mg, Lisinopril 10mg | HbA1c 8.2% |
| **James Kowalski** (PID=2) | COPD, AFib, T2DM | Tiotropium, Apixaban 5mg, Metformin | BNP 385 |
| **Aisha Patel** (PID=3) | MDD, Hypothyroidism | Sertraline 100mg, Levothyroxine 75mcg | TSH 6.8 |

To use a different patient, change `patient_id` in the `page_context`.

---

## Tips for Manual Testing

1. **Keep both terminals open:**
   - Terminal 1: Agent API logs
   - Terminal 2: Your curl commands

2. **Use `jq` for readable output:**
   ```bash
   curl ... | jq '.response'  # Just the agent's response
   curl ... | jq '.manifest'   # Just the manifest
   curl ... | jq .            # Full response
   ```

3. **Save your session ID:**
   ```bash
   export SESSION_ID="your-session-uuid"
   # Then reuse: curl ... -d "{\"session_id\": \"$SESSION_ID\", ...}"
   ```

4. **Check Jaeger traces after each chat:**
   - Go to http://localhost:16686
   - Look for new traces with tags like `session_id=$SESSION_ID`
   - Inspect for errors, token counts, latency

5. **Verify writes in the browser:**
   - After executing a manifest, refresh OpenEMR
   - Check the relevant tab (medications, allergies, problems, etc.)
   - Verify the change was actually persisted

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| OpenEMR login fails | Wait 1-2 more minutes for startup. Check `docker logs week2_emr_agent-openemr-1` |
| "No patients found" in OpenEMR | Seed data wasn't loaded. Run `python scripts/seed_fhir.py` inside the agent container |
| Agent says "Cannot read patient" | Patient ID is wrong, or OpenEMR isn't responding. Check `/api/health` |
| Manifest won't execute | Item was rejected, not approved. Approve first, then execute |
| Can't see changes in OpenEMR | Refresh the page. If still missing, check `execution_result` in manifest for errors |
| Jaeger shows no traces | Restart agent with `--reload`. Check `OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317` in logs |

---

## Next: Phase 2 (Browser UI)

Once implemented, the agent chat will be **embedded in OpenEMR** so you won't need curl. You'll just click a chat button on any patient's chart and interact with the agent directly in the browser.
