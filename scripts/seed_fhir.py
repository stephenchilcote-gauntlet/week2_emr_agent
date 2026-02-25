"""Seed OpenEMR with test patients and clinical data via the FHIR and REST APIs.

Patients are created via FHIR. Conditions are created via the REST API
(OpenEMR's FHIR Condition resource only supports read, not create).
"""

from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "http://localhost:80"
FHIR_URL = f"{BASE_URL}/apis/default/fhir"
REST_URL = f"{BASE_URL}/apis/default/api"
TOKEN_URL = f"{BASE_URL}/oauth2/default/token"

SCOPES = (
    "openid api:oemr api:fhir "
    "user/Patient.read user/Patient.write "
    "user/Condition.read "
    "user/patient.read user/patient.write "
    "user/medical_problem.read user/medical_problem.write "
    "user/Observation.read user/vital.read user/vital.write "
    "user/MedicationRequest.read user/medication.read user/medication.write "
    "user/Encounter.read user/encounter.read user/encounter.write "
    "user/AllergyIntolerance.read user/allergy.read user/allergy.write"
)

PATIENTS = [
    {"family": "Santos", "given": "Maria", "gender": "female", "birthDate": "1985-03-14"},
    {"family": "Kowalski", "given": "James", "gender": "male", "birthDate": "1958-11-02"},
    {"family": "Patel", "given": "Aisha", "gender": "female", "birthDate": "1972-07-28"},
]

CONDITIONS: dict[str, list[tuple[str, str]]] = {
    "Maria Santos": [
        ("E11.9", "Type 2 Diabetes Mellitus"),
        ("I10", "Essential Hypertension"),
    ],
    "James Kowalski": [
        ("J44.1", "Chronic Obstructive Pulmonary Disease with Acute Exacerbation"),
        ("I48.91", "Unspecified Atrial Fibrillation"),
        ("E11.65", "Type 2 Diabetes Mellitus with Hyperglycemia"),
    ],
    "Aisha Patel": [
        ("F33.1", "Major Depressive Disorder, Recurrent, Moderate"),
        ("E03.9", "Hypothyroidism, Unspecified"),
    ],
}


def get_token(client: httpx.Client) -> str:
    client_id = os.environ.get("OPENEMR_CLIENT_ID", "")
    client_secret = os.environ.get("OPENEMR_CLIENT_SECRET", "")

    if not client_id:
        print("ERROR: OPENEMR_CLIENT_ID not set")
        sys.exit(1)

    print(f"Requesting OAuth2 token from {TOKEN_URL} ...")
    resp = client.post(
        TOKEN_URL,
        data={
            "grant_type": "password",
            "username": "admin",
            "password": "pass",
            "client_id": client_id,
            "client_secret": client_secret,
            "user_role": "users",
            "scope": SCOPES,
        },
    )
    if resp.status_code >= 400:
        print(f"  ✗ Token request failed: HTTP {resp.status_code}")
        print(f"  Response: {resp.text[:500]}")
        sys.exit(1)
    token = resp.json()["access_token"]
    print("  ✓ Token acquired")
    return token


def create_patient(client: httpx.Client, headers: dict, pat: dict) -> str:
    resource = {
        "resourceType": "Patient",
        "name": [{"use": "official", "family": pat["family"], "given": [pat["given"]]}],
        "gender": pat["gender"],
        "birthDate": pat["birthDate"],
    }
    print(f"Creating patient {pat['given']} {pat['family']} ...")
    resp = client.post(f"{FHIR_URL}/Patient", json=resource, headers=headers)
    if resp.status_code >= 400:
        print(f"  ✗ HTTP {resp.status_code}: {resp.text[:300]}")
        return ""
    body = resp.json()
    # OpenEMR returns {"pid": N, "uuid": "..."} for FHIR Patient create
    patient_id = body.get("uuid") or body.get("id", "")
    print(f"  ✓ Patient created: id={patient_id}")
    return patient_id


def create_condition(
    client: httpx.Client,
    headers: dict,
    patient_uuid: str,
    code: str,
    display: str,
) -> None:
    """Create a condition via the REST API medical_problem endpoint.

    OpenEMR's FHIR Condition resource only supports read/search,
    so we use POST /api/patient/{uuid}/medical_problem instead.
    """
    payload = {
        "title": display,
        "diagnosis": f"ICD10:{code}",
        "begdate": "2024-01-01",
    }
    print(f"  Creating condition {code} ({display}) ...")
    resp = client.post(
        f"{REST_URL}/patient/{patient_uuid}/medical_problem",
        json=payload,
        headers=headers,
    )
    if resp.status_code >= 400:
        print(f"    ✗ HTTP {resp.status_code}: {resp.text[:300]}")
    else:
        body = resp.json()
        cid = body.get("data", {}).get("uuid", body.get("data", {}).get("id", "?"))
        print(f"    ✓ Condition created: id={cid}")


def main() -> None:
    print("=" * 60)
    print("OpenEMR FHIR Seed Script")
    print("=" * 60)

    with httpx.Client(timeout=30.0) as client:
        token = get_token(client)
        headers = {"Authorization": f"Bearer {token}"}

        patient_ids: dict[str, str] = {}
        for pat in PATIENTS:
            full_name = f"{pat['given']} {pat['family']}"
            pid = create_patient(client, headers, pat)
            if pid:
                patient_ids[full_name] = pid

        print()
        print("Creating conditions ...")
        for name, conditions in CONDITIONS.items():
            pid = patient_ids.get(name)
            if not pid:
                print(f"  Skipping conditions for {name} (patient not created)")
                continue
            print(f"  Patient: {name} (id={pid})")
            for code, display in conditions:
                create_condition(client, headers, pid, code, display)

        print()
        print("=" * 60)
        print(f"Seed complete. {len(patient_ids)} patients created.")
        print("=" * 60)


if __name__ == "__main__":
    main()
