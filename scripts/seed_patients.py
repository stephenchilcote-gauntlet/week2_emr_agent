"""Seed OpenEMR with comprehensive synthetic patient data for E2E eval tests.

Creates (or overwrites) 10 synthetic patients:
  EVAL TEST PATIENTS (must match tests/eval/dataset.json PIDs 4-6):
  - Maria Santos    (PID 4) — T2DM, HTN, obesity, hyperlipidemia
  - James Kowalski  (PID 5) — COPD, AFib, T2DM, CKD
  - Aisha Patel     (PID 6) — depression, hypothyroidism, GAD

  ADDITIONAL RICH PATIENTS (for manifest apply testing):
  - Robert Chen     (PID 7) — heart failure, AFib, hyperlipidemia
  - Elena Rodriguez (PID 8) — rheumatoid arthritis, hypothyroidism
  - Michael Thompson(PID 9) — CKD stage 4, T2DM, anemia
  - Sarah Kim       (PID 10)— SLE, lupus nephritis, depression
  - William Davis   (PID 11)— COPD, lung cancer, DVT
  - Linda Martinez  (PID 12)— MDD, prediabetes, insomnia
  - David Brown     (PID 13)— Parkinson's, dementia, HTN

Run before E2E tests. Safe to re-run — clears and re-seeds clinical data each time.

NOTE: Conditions and medications are cleared via direct SQL because the
OpenEMR REST list endpoints for these resources return empty even when
records exist (known OpenEMR bug). Allergies use the REST API.

Usage:
    uv run python scripts/seed_patients.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

BASE_URL = os.environ.get("OPENEMR_URL", "http://localhost:80")
FHIR_URL = f"{BASE_URL}/apis/default/fhir"
REST_URL = f"{BASE_URL}/apis/default/api"
TOKEN_URL = f"{BASE_URL}/oauth2/default/token"

CLIENT_ID = os.environ.get("OPENEMR_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("OPENEMR_CLIENT_SECRET", "")
ADMIN_USER = os.environ.get("OPENEMR_USER", "admin")
ADMIN_PASS = os.environ.get("OPENEMR_PASS", "pass")

DOCKER_CONTAINER = os.environ.get("OPENEMR_CONTAINER", "week2_emr_agent-openemr-1")
DB_USER = os.environ.get("OPENEMR_DB_USER", "openemr")
DB_PASS = os.environ.get("OPENEMR_DB_PASS", "openemr")
DB_NAME = os.environ.get("OPENEMR_DB_NAME", "openemr")

SCOPES = (
    "openid api:oemr api:fhir "
    "user/Patient.read user/Patient.write "
    "user/Condition.read "
    "user/Observation.read "
    "user/MedicationRequest.read "
    "user/Encounter.read "
    "user/AllergyIntolerance.read "
    "user/patient.read user/patient.write "
    "user/medical_problem.read user/medical_problem.write "
    "user/allergy.read user/allergy.write "
    "user/medication.read user/medication.write "
    "user/encounter.read user/encounter.write "
    "user/vital.read user/vital.write"
)

# -------------------------------------------------------------------------
# Synthetic patient definitions
# -------------------------------------------------------------------------

PATIENTS: list[dict] = [
    # ── Maria Santos ─────────────────────────────────────────────────────
    # Expected PID = 4. T2DM, HTN, obesity, hyperlipidemia.
    # Eval cases: hp-02 (diabetes/HTN), hp-03 (metformin/lisinopril), hp-05,
    # hp-13, hp-14, hp-17, dsl-03, dsl-06, dsl-08, dsl-16, cp-02, cp-05, cp-11
    {
        "fname": "Maria",
        "lname": "Santos",
        "gender": "female",
        "dob": "1985-03-14",
        "conditions": [
            # (title, ICD10 code, begdate)
            ("Type 2 Diabetes Mellitus", "E11.9", "2020-01-15"),
            ("Essential Hypertension", "I10", "2019-06-01"),
            ("Obesity", "E66.01", "2021-03-10"),
            ("Hyperlipidemia", "E78.5", "2020-08-15"),
        ],
        # Medications: (title, begdate) — title is the full drug+dose+freq string
        "medications": [
            ("Metformin 500mg twice daily", "2020-02-01"),
            ("Lisinopril 10mg daily", "2019-07-01"),
        ],
        "allergies": [],
        "encounter_reason": "Diabetes follow-up",
    },
    # ── James Kowalski ───────────────────────────────────────────────────
    # Expected PID = 5. COPD, AFib, T2DM with hyperglycemia, CKD stage 3.
    # Eval cases: hp-07 (apixaban), hp-09 (copd/tiotropium), hp-12, hp-16,
    # dsl-05, dsl-08, dsl-14, cp-01, cp-06, cp-07, cp-08, cp-12
    {
        "fname": "James",
        "lname": "Kowalski",
        "gender": "male",
        "dob": "1958-11-02",
        "conditions": [
            ("Chronic Obstructive Pulmonary Disease with Acute Exacerbation", "J44.1", "2018-03-10"),
            ("Unspecified Atrial Fibrillation", "I48.91", "2019-09-15"),
            ("Type 2 Diabetes Mellitus with Hyperglycemia", "E11.65", "2021-05-20"),
            ("Chronic Kidney Disease, Stage 3", "N18.3", "2022-11-01"),
        ],
        "medications": [
            ("Tiotropium 18mcg inhaled daily", "2018-04-01"),
            ("Apixaban 5mg twice daily", "2019-10-01"),
            ("Metformin 1000mg twice daily", "2021-06-01"),
            ("Albuterol 90mcg inhaled PRN", "2018-04-01"),
        ],
        "allergies": [],
        "encounter_reason": "COPD and atrial fibrillation management",
    },
    # ── Aisha Patel ──────────────────────────────────────────────────────
    # Expected PID = 6. Depression, hypothyroidism, GAD. Penicillin allergy.
    # Eval cases: hp-10, hp-15, hp-18, hp-19, dsl-01, dsl-07, dsl-15,
    # cp-03 (sertraline+tramadol), cp-04, cp-09, oq-08, oq-10, oq-12
    {
        "fname": "Aisha",
        "lname": "Patel",
        "gender": "female",
        "dob": "1972-07-28",
        "conditions": [
            ("Major Depressive Disorder, Recurrent, Moderate", "F33.1", "2022-02-14"),
            ("Hypothyroidism, Unspecified", "E03.9", "2021-08-05"),
            ("Generalized Anxiety Disorder", "F41.1", "2022-03-01"),
        ],
        "medications": [
            ("Sertraline 100mg daily", "2022-03-01"),
            ("Levothyroxine 75mcg daily", "2021-09-01"),
        ],
        "allergies": [
            # (title, type, note)
            ("Penicillin", "allergy", "Rash and hives"),
        ],
        "encounter_reason": "Depression and thyroid management follow-up",
    },
    # ── Robert Chen ──────────────────────────────────────────────────────
    # PID = 7. Heart failure with preserved EF, AFib, HTN, hyperlipidemia.
    # Complex polypharmacy patient for manifest testing.
    {
        "fname": "Robert",
        "lname": "Chen",
        "gender": "male",
        "dob": "1952-08-19",
        "conditions": [
            ("Heart Failure with Preserved Ejection Fraction", "I50.3", "2021-04-10"),
            ("Unspecified Atrial Fibrillation", "I48.91", "2020-07-15"),
            ("Essential Hypertension", "I10", "2018-01-20"),
            ("Pure Hypercholesterolemia", "E78.00", "2019-03-05"),
        ],
        "medications": [
            ("Carvedilol 25mg twice daily", "2021-05-01"),
            ("Furosemide 40mg daily", "2021-04-15"),
            ("Spironolactone 25mg daily", "2021-04-15"),
            ("Atorvastatin 80mg daily", "2019-03-10"),
            ("Rivaroxaban 20mg daily", "2020-08-01"),
        ],
        "allergies": [
            ("Amiodarone", "allergy", "Thyroid dysfunction"),
        ],
        "encounter_reason": "Heart failure follow-up and fluid management",
    },
    # ── Elena Rodriguez ──────────────────────────────────────────────────
    # PID = 8. Rheumatoid arthritis, hypothyroidism, secondary Sjogren's.
    # Immunosuppressed patient on methotrexate.
    {
        "fname": "Elena",
        "lname": "Rodriguez",
        "gender": "female",
        "dob": "1979-12-03",
        "conditions": [
            ("Rheumatoid Arthritis, Unspecified", "M06.9", "2017-09-22"),
            ("Hypothyroidism, Unspecified", "E03.9", "2019-04-11"),
            ("Secondary Sjogren Syndrome", "M35.03", "2020-01-08"),
        ],
        "medications": [
            ("Methotrexate 15mg weekly", "2017-10-15"),
            ("Folic Acid 1mg daily", "2017-10-15"),
            ("Hydroxychloroquine 400mg daily", "2018-02-01"),
            ("Levothyroxine 50mcg daily", "2019-05-01"),
            ("Prednisone 5mg daily", "2021-06-01"),
        ],
        "allergies": [
            ("Sulfonamides", "allergy", "Diffuse maculopapular rash"),
        ],
        "encounter_reason": "Rheumatoid arthritis and immunosuppression monitoring",
    },
    # ── Michael Thompson ─────────────────────────────────────────────────
    # PID = 9. CKD stage 4, T2DM, hypertension, anemia of CKD.
    # Renal dose adjustment scenarios.
    {
        "fname": "Michael",
        "lname": "Thompson",
        "gender": "male",
        "dob": "1966-05-30",
        "conditions": [
            ("Chronic Kidney Disease, Stage 4", "N18.4", "2020-02-14"),
            ("Type 2 Diabetes Mellitus", "E11.9", "2016-07-20"),
            ("Essential Hypertension", "I10", "2015-03-18"),
            ("Anemia in Chronic Kidney Disease", "N18.9", "2021-01-10"),
        ],
        "medications": [
            ("Amlodipine 10mg daily", "2015-04-01"),
            ("Insulin Glargine 20 units at bedtime", "2020-08-01"),
            ("Sodium Bicarbonate 650mg twice daily", "2021-03-01"),
            ("Epoetin Alfa 4000 units subcutaneous 3x weekly", "2021-02-01"),
        ],
        "allergies": [
            ("Iodinated Contrast Dye", "allergy", "Acute kidney injury"),
        ],
        "encounter_reason": "CKD management and anemia follow-up",
    },
    # ── Sarah Kim ────────────────────────────────────────────────────────
    # PID = 10. Systemic lupus erythematosus, lupus nephritis, depression.
    # Young female on immunosuppression with multiple drug allergies.
    {
        "fname": "Sarah",
        "lname": "Kim",
        "gender": "female",
        "dob": "1996-02-17",
        "conditions": [
            ("Systemic Lupus Erythematosus", "M32.9", "2019-11-05"),
            ("Lupus Nephritis", "M32.14", "2020-03-15"),
            ("Major Depressive Disorder, Single Episode, Moderate", "F32.1", "2021-01-20"),
        ],
        "medications": [
            ("Hydroxychloroquine 200mg twice daily", "2019-11-20"),
            ("Mycophenolate Mofetil 1g twice daily", "2020-04-01"),
            ("Prednisone 10mg daily", "2020-03-20"),
            ("Sertraline 50mg daily", "2021-02-01"),
        ],
        "allergies": [
            ("Penicillin", "allergy", "Anaphylaxis"),
            ("NSAIDs", "allergy", "Lupus flare and renal deterioration"),
        ],
        "encounter_reason": "Lupus nephritis monitoring and mental health follow-up",
    },
    # ── William Davis ─────────────────────────────────────────────────────
    # PID = 11. COPD, non-small cell lung cancer on immunotherapy, DVT.
    # Complex oncology patient on anticoagulation.
    {
        "fname": "William",
        "lname": "Davis",
        "gender": "male",
        "dob": "1959-09-14",
        "conditions": [
            ("Chronic Obstructive Pulmonary Disease, Unspecified", "J44.0", "2016-05-08"),
            ("Malignant Neoplasm of Upper Lobe, Right Bronchus/Lung", "C34.10", "2023-02-20"),
            ("Deep Vein Thrombosis of Right Leg", "I82.401", "2023-08-15"),
        ],
        "medications": [
            ("Tiotropium 18mcg inhaled daily", "2016-06-01"),
            ("Pembrolizumab 200mg IV every 3 weeks", "2023-03-15"),
            ("Rivaroxaban 15mg twice daily", "2023-08-20"),
            ("Dexamethasone 4mg orally before infusion", "2023-03-15"),
        ],
        "allergies": [
            ("Codeine", "allergy", "Severe respiratory depression"),
        ],
        "encounter_reason": "Lung cancer immunotherapy monitoring and DVT management",
    },
    # ── Linda Martinez ────────────────────────────────────────────────────
    # PID = 12. MDD, prediabetes, insomnia. Starting antidepressant therapy.
    # Mental health and metabolic syndrome patient.
    {
        "fname": "Linda",
        "lname": "Martinez",
        "gender": "female",
        "dob": "1969-11-25",
        "conditions": [
            ("Major Depressive Disorder, Single Episode, Moderate", "F32.1", "2023-01-10"),
            ("Prediabetes", "R73.09", "2022-09-20"),
            ("Insomnia, Unspecified", "G47.00", "2023-01-10"),
        ],
        "medications": [
            ("Venlafaxine 150mg daily", "2023-02-01"),
            ("Metformin 500mg twice daily", "2023-01-15"),
            ("Trazodone 50mg at bedtime", "2023-01-15"),
        ],
        "allergies": [],
        "encounter_reason": "Depression management and metabolic monitoring",
    },
    # ── David Brown ───────────────────────────────────────────────────────
    # PID = 13. Parkinson's disease, Alzheimer's dementia, HTN, osteoporosis.
    # Elderly patient with complex neurodegenerative conditions.
    {
        "fname": "David",
        "lname": "Brown",
        "gender": "male",
        "dob": "1944-04-07",
        "conditions": [
            ("Parkinson's Disease", "G20", "2018-06-15"),
            ("Alzheimer's Disease, Unspecified", "G30.9", "2020-10-01"),
            ("Essential Hypertension", "I10", "2010-03-22"),
            ("Osteoporosis without Current Pathological Fracture", "M81.0", "2019-08-14"),
        ],
        "medications": [
            ("Carbidopa-Levodopa 25/100mg three times daily", "2018-07-01"),
            ("Memantine 10mg twice daily", "2020-11-01"),
            ("Donepezil 10mg daily at bedtime", "2020-11-01"),
            ("Lisinopril 5mg daily", "2010-04-01"),
            ("Alendronate 70mg weekly", "2019-09-01"),
        ],
        "allergies": [
            ("Haloperidol", "allergy", "Severe worsening of parkinsonian symptoms"),
        ],
        "encounter_reason": "Parkinson's disease and dementia management",
    },
]

# -------------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------------


def _safe_json(resp: httpx.Response) -> dict | list | None:
    """Return parsed JSON or None if body is empty/invalid."""
    if not resp.content:
        return None
    try:
        return resp.json()
    except Exception:
        return None


def get_token(client: httpx.Client) -> str:
    if not CLIENT_ID:
        print("ERROR: OPENEMR_CLIENT_ID not set. Export it or set in .env")
        sys.exit(1)
    resp = client.post(
        TOKEN_URL,
        data={
            "grant_type": "password",
            "username": ADMIN_USER,
            "password": ADMIN_PASS,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "user_role": "users",
            "scope": SCOPES,
        },
    )
    if resp.status_code >= 400:
        print(f"  ✗ Token request failed: HTTP {resp.status_code}: {resp.text[:400]}")
        sys.exit(1)
    token = resp.json()["access_token"]
    print("  ✓ OAuth2 token acquired")
    return token


# -------------------------------------------------------------------------
# Patient lookup / create
# -------------------------------------------------------------------------


def find_patient_by_name(
    client: httpx.Client, headers: dict, fname: str, lname: str,
) -> tuple[str | None, str | None]:
    """Return (pid, uuid) for an existing patient, or (None, None)."""
    resp = client.get(
        f"{REST_URL}/patient",
        params={"fname": fname, "lname": lname},
        headers=headers,
    )
    body = _safe_json(resp) or {}
    patients = body.get("data", []) if isinstance(body, dict) else []
    if not isinstance(patients, list):
        patients = []
    for p in patients:
        if (
            p.get("fname", "").lower() == fname.lower()
            and p.get("lname", "").lower() == lname.lower()
        ):
            return str(p["pid"]), str(p["uuid"])
    return None, None


def create_patient_fhir(
    client: httpx.Client, headers: dict, pat: dict,
) -> tuple[str | None, str | None]:
    """Create a patient via FHIR API. Returns (pid, uuid)."""
    resource = {
        "resourceType": "Patient",
        "name": [{"use": "official", "family": pat["lname"], "given": [pat["fname"]]}],
        "gender": pat["gender"],
        "birthDate": pat["dob"],
    }
    resp = client.post(f"{FHIR_URL}/Patient", json=resource, headers=headers)
    if resp.status_code >= 400:
        print(f"    ✗ FHIR Patient create failed: HTTP {resp.status_code}: {resp.text[:300]}")
        return None, None
    body = _safe_json(resp) or {}
    pid = str(body.get("pid") or "")
    uuid = str(body.get("uuid") or body.get("id") or "")
    return pid or None, uuid or None


def ensure_patient(
    client: httpx.Client, headers: dict, pat: dict,
) -> tuple[str | None, str | None]:
    """Find or create a patient. Returns (pid, uuid)."""
    fname, lname = pat["fname"], pat["lname"]
    pid, uuid = find_patient_by_name(client, headers, fname, lname)
    if pid and uuid:
        print(f"  → Found: {fname} {lname} (PID={pid}, UUID={uuid})")
        return pid, uuid
    print(f"  → Creating: {fname} {lname} ...")
    pid, uuid = create_patient_fhir(client, headers, pat)
    if pid:
        print(f"    ✓ Created: PID={pid}, UUID={uuid}")
    return pid, uuid


# -------------------------------------------------------------------------
# Conditions — clear via SQL (REST GET returns empty despite records existing)
# -------------------------------------------------------------------------


def clear_conditions_sql(pid: str) -> None:
    """Delete all conditions for a patient via SQL.

    NOTE: The OpenEMR REST endpoint GET /patient/{uuid}/medical_problem returns
    an empty list even when conditions exist in the database. Direct SQL is
    required for reliable idempotent clearing.
    """
    sql = f"DELETE FROM lists WHERE type='medical_problem' AND pid={int(pid)};"
    ok = _run_sql(sql)
    if ok:
        print(f"    ✓ Cleared existing conditions (PID={pid})")


def seed_conditions(
    client: httpx.Client, headers: dict, uuid: str, conditions: list[tuple],
) -> None:
    for title, icd10, begdate in conditions:
        payload = {
            "title": title,
            "diagnosis": f"ICD10:{icd10}",
            "begdate": begdate,
        }
        resp = client.post(
            f"{REST_URL}/patient/{uuid}/medical_problem",
            json=payload,
            headers=headers,
        )
        body = _safe_json(resp)
        if resp.status_code >= 400:
            print(f"    ✗ Condition {title}: HTTP {resp.status_code}")
            continue
        if isinstance(body, dict) and body.get("validationErrors"):
            print(f"    ✗ Condition {title}: {body['validationErrors']}")
            continue
        cid = "?"
        if isinstance(body, dict):
            data = body.get("data") or {}
            cid = data.get("uuid") or data.get("id") or "?"
        print(f"    ✓ Condition: {title} ({icd10}) id={cid}")


# -------------------------------------------------------------------------
# Medications — use direct SQL via Docker (REST API creates NULL UUIDs which
# break the FHIR MedicationRequest endpoint)
# -------------------------------------------------------------------------


def _run_sql(sql: str) -> bool:
    """Execute SQL in the OpenEMR container via Docker exec."""
    cmd = [
        "docker", "exec", DOCKER_CONTAINER,
        "bash", "-c",
        f"mariadb -u {DB_USER} --password={DB_PASS} {DB_NAME} --skip-ssl -e {sql!r}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            print(f"    ✗ SQL error: {result.stderr.strip()[:200]}")
            return False
        return True
    except Exception as exc:
        print(f"    ✗ Docker exec failed: {exc}")
        return False


def clear_medications_sql(pid: str) -> None:
    """Delete all medications for a patient via SQL."""
    sql = f"DELETE FROM lists WHERE type='medication' AND pid={int(pid)};"
    ok = _run_sql(sql)
    if ok:
        print(f"    ✓ Cleared existing medications (PID={pid})")


def seed_medications_sql(pid: str, medications: list[tuple]) -> None:
    """Insert medications via SQL with proper UUIDs so FHIR can read them."""
    for title, _ in medications:
        # Generate a UUID and format as 32-char hex for BINARY(16) column
        med_uuid = uuid.uuid4().hex.upper()
        # Escape single quotes in title
        safe_title = title.replace("'", "\\'")
        sql = (
            f"INSERT INTO lists (type, pid, title, begdate, uuid) "
            f"VALUES ('medication', {int(pid)}, '{safe_title}', NULL, UNHEX('{med_uuid}'));"
        )
        ok = _run_sql(sql)
        if ok:
            print(f"    ✓ Medication: {title}")


# -------------------------------------------------------------------------
# Allergies — clear via SQL, seed via REST
# -------------------------------------------------------------------------


def clear_allergies_sql(pid: str) -> None:
    """Delete all allergies for a patient via SQL."""
    sql = f"DELETE FROM lists WHERE type='allergy' AND pid={int(pid)};"
    ok = _run_sql(sql)
    if ok:
        print(f"    ✓ Cleared existing allergies (PID={pid})")


def seed_allergies(
    client: httpx.Client, headers: dict, uuid: str, allergies: list[tuple],
) -> None:
    for title, allergy_type, note in allergies:
        payload = {
            "title": title,
            "comments": note,
        }
        resp = client.post(
            f"{REST_URL}/patient/{uuid}/allergy",
            json=payload,
            headers=headers,
        )
        body = _safe_json(resp)
        if resp.status_code >= 400:
            print(f"    ✗ Allergy {title}: HTTP {resp.status_code}")
            continue
        if isinstance(body, dict) and body.get("validationErrors"):
            print(f"    ✗ Allergy {title}: {body['validationErrors']}")
            continue
        aid = "?"
        if isinstance(body, dict):
            data = body.get("data") or {}
            aid = data.get("id") or data.get("uuid") or "?"
        print(f"    ✓ Allergy: {title} id={aid}")


# -------------------------------------------------------------------------
# Encounter — use UUID in path
# -------------------------------------------------------------------------


def get_or_create_encounter(
    client: httpx.Client, headers: dict, pid: str, uuid: str, reason: str,
) -> str | None:
    """Return the encounter ID, creating one if none exists."""
    resp = client.get(f"{REST_URL}/patient/{pid}/encounter", headers=headers)
    body = _safe_json(resp)
    if body:
        encounters = body.get("data", []) if isinstance(body, dict) else body
        if isinstance(encounters, list) and encounters:
            enc = encounters[0]
            eid = str(enc.get("encounter") or enc.get("id") or "")
            if eid:
                print(f"    → Using existing encounter (eid={eid})")
                return eid

    payload = {
        "date": "2025-01-15",
        "onset_date": "",
        "reason": reason,
        "facility": "Family Medicine Clinic",
        "facility_id": 3,
        "billing_facility": 3,
        "sensitivity": "normal",
        "pos_code": "11",
        "pc_catid": 5,
        "class_code": "AMB",
    }
    resp = client.post(
        f"{REST_URL}/patient/{uuid}/encounter",
        json=payload,
        headers=headers,
    )
    body = _safe_json(resp)
    if resp.status_code >= 400 or not body:
        print(f"    ✗ Encounter create: HTTP {resp.status_code}")
        return None
    if isinstance(body, dict) and body.get("validationErrors"):
        print(f"    ✗ Encounter create: {body['validationErrors']}")
        return None
    data = body.get("data", {}) if isinstance(body, dict) else {}
    eid = str(data.get("encounter") or data.get("id") or "")
    if eid:
        print(f"    ✓ Encounter created (eid={eid})")
    return eid or None


# -------------------------------------------------------------------------
# Main patient seed logic
# -------------------------------------------------------------------------


def seed_patient(client: httpx.Client, headers: dict, pat: dict) -> None:
    fname, lname = pat["fname"], pat["lname"]
    print(f"\n{'─'*60}")
    print(f"  {fname} {lname}")
    print(f"{'─'*60}")

    pid, uuid = ensure_patient(client, headers, pat)
    if not pid or not uuid:
        print(f"  ✗ Failed to create/find patient, skipping")
        return

    print(f"\n  Conditions:")
    clear_conditions_sql(pid)
    seed_conditions(client, headers, uuid, pat["conditions"])

    print(f"\n  Medications (via SQL):")
    clear_medications_sql(pid)
    seed_medications_sql(pid, pat["medications"])

    print(f"\n  Allergies:")
    clear_allergies_sql(pid)
    if pat.get("allergies"):
        seed_allergies(client, headers, uuid, pat["allergies"])

    print(f"\n  Encounter:")
    get_or_create_encounter(
        client, headers, pid, uuid, pat.get("encounter_reason", "Office visit"),
    )

    print(f"\n  ✓ {fname} {lname} (PID={pid}) seeded successfully")


def verify_pids(client: httpx.Client, headers: dict) -> None:
    """Verify all patients with their PIDs."""
    print(f"\n{'='*60}")
    print("PID verification")
    print(f"{'='*60}")
    # The first 3 must match dataset.json exactly
    eval_patients = [
        ("Maria", "Santos", 4),
        ("James", "Kowalski", 5),
        ("Aisha", "Patel", 6),
    ]
    all_ok = True
    eval_ok = True
    for fname, lname, expected_pid in eval_patients:
        pid, uuid = find_patient_by_name(client, headers, fname, lname)
        if pid is None:
            print(f"  ✗ {fname} {lname}: NOT FOUND")
            all_ok = False
            eval_ok = False
        elif int(pid) != expected_pid:
            print(
                f"  ✗ {fname} {lname}: PID={pid} (expected {expected_pid}) — "
                f"update dataset.json if needed!"
            )
            all_ok = False
            eval_ok = False
        else:
            print(f"  ✓ {fname} {lname}: PID={pid}")

    # Additional patients — just print their PIDs (no expected value)
    extra_patients = [
        ("Robert", "Chen"),
        ("Elena", "Rodriguez"),
        ("Michael", "Thompson"),
        ("Sarah", "Kim"),
        ("William", "Davis"),
        ("Linda", "Martinez"),
        ("David", "Brown"),
    ]
    for fname, lname in extra_patients:
        pid, _ = find_patient_by_name(client, headers, fname, lname)
        if pid:
            print(f"  ✓ {fname} {lname}: PID={pid}")
        else:
            print(f"  ✗ {fname} {lname}: NOT FOUND")
            all_ok = False

    if not eval_ok:
        print(
            "\nWARNING: Eval patient PIDs don't match dataset.json. "
            "The eval dataset assumes PIDs 4, 5, 6."
        )


def main() -> None:
    print("=" * 60)
    print("OpenEMR Patient Seed Script")
    print(f"Target: {BASE_URL}")
    print("=" * 60)

    with httpx.Client(timeout=30.0) as client:
        print("\nAuthenticating ...")
        token = get_token(client)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        for pat in PATIENTS:
            seed_patient(client, headers, pat)

        verify_pids(client, headers)

    print(f"\n{'='*60}")
    print(f"Seed complete. {len(PATIENTS)} patients processed.")
    print("  PIDs 4-6: eval test patients (Maria Santos, James Kowalski, Aisha Patel)")
    print("  PIDs 7-13: additional manifest-testing patients")
    print("=" * 60)


if __name__ == "__main__":
    main()
