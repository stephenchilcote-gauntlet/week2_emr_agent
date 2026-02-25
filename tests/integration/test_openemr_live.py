"""Integration tests against a running OpenEMR instance."""

from __future__ import annotations

import subprocess

import anthropic
import pytest

from src.agent.loop import AgentLoop
from src.agent.models import (
    AgentSession,
    ChangeManifest,
    ManifestAction,
    ManifestItem,
    PageContext,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

PATIENT_PID = "5"
PATIENT_UUID = "a1270f97-9d80-4ba9-aa04-7b8ec7b8811e"


def _db_query(sql: str) -> str:
    result = subprocess.run(
        [
            "docker", "exec", "week2_emr_agent-mysql-1",
            "mysql", "-uopenemr", "-popenemr", "openemr",
            "-e", sql,
        ],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"MySQL query failed: {result.stderr}")
    return result.stdout


async def test_health_fhir_metadata(live_client):
    """FHIR metadata endpoint returns a CapabilityStatement."""
    result = await live_client.get_fhir_metadata()
    assert "error" not in result, f"metadata request failed: {result}"
    assert result.get("resourceType") == "CapabilityStatement"


async def test_auth_token_acquisition(live_client):
    """Client can acquire an OAuth2 access token."""
    await live_client._ensure_auth()
    assert live_client._access_token is not None
    assert len(live_client._access_token) > 0


async def test_fhir_read_patients(live_client):
    """Reading Patient resources returns a FHIR Bundle."""
    result = await live_client.fhir_read("Patient")
    assert "error" not in result, f"Patient read failed: {result}"
    assert result.get("resourceType") == "Bundle"
    assert "entry" in result or result.get("total", 0) >= 0


async def test_fhir_read_conditions(live_client):
    """Reading Conditions for a known patient returns a Bundle."""
    # First, find a patient
    patients = await live_client.fhir_read("Patient")
    assert "error" not in patients, f"Patient read failed: {patients}"
    entries = patients.get("entry", [])
    assert len(entries) > 0, "No patients found — run seed_fhir.py first"

    patient_id = entries[0]["resource"]["id"]
    result = await live_client.fhir_read("Condition", params={"patient": patient_id})
    assert "error" not in result, f"Condition read failed: {result}"
    assert result.get("resourceType") == "Bundle"


async def test_api_call_patient(live_client):
    """REST API patient endpoint returns a list of patients."""
    result = await live_client.api_call("patient")
    assert "error" not in result, f"API call failed: {result}"
    # The REST API returns a list of patient dicts
    assert isinstance(result, (list, dict))


async def test_fhir_write_and_read_roundtrip(live_client):
    """Create a condition via REST API, then read it back via FHIR.

    OpenEMR's FHIR Condition resource only supports read/search, so we
    create via POST /api/patient/{uuid}/medical_problem and verify the
    condition appears in the FHIR Condition search results.
    """
    # Get a patient to attach the condition to
    patients = await live_client.fhir_read("Patient")
    assert "error" not in patients, f"Patient read failed: {patients}"
    entries = patients.get("entry", [])
    assert len(entries) > 0, "No patients found — run seed_fhir.py first"
    patient_id = entries[0]["resource"]["id"]

    # Write via REST API (medical_problem)
    write_result = await live_client.api_call(
        f"patient/{patient_id}/medical_problem",
        method="POST",
        payload={
            "title": "Integration Test Condition",
            "diagnosis": "ICD10:Z00.00",
            "begdate": "2024-06-01",
        },
    )
    assert "error" not in write_result, f"Condition write failed: {write_result}"
    condition_uuid = write_result.get("data", {}).get("uuid")
    assert condition_uuid, f"No uuid in write response: {write_result}"

    # Read back via FHIR
    read_result = await live_client.fhir_read(
        "Condition", params={"patient": patient_id}
    )
    assert "error" not in read_result, f"Condition read-back failed: {read_result}"
    assert read_result.get("resourceType") == "Bundle"

    # Verify our condition is in the results
    found_ids = [
        e["resource"]["id"]
        for e in read_result.get("entry", [])
        if e.get("resource", {}).get("id")
    ]
    assert condition_uuid in found_ids, (
        f"Created condition {condition_uuid} not found in read-back. Found: {found_ids}"
    )


async def test_medication_update_preserves_drug_name(live_client):
    """Updating a medication's dose preserves the drug name in the title.

    Regression test for the bug where a partial update (dose-only) would
    overwrite the title with just the dose string (e.g. "500mg twice daily"
    instead of "Metformin 500mg twice daily").
    """
    # Get existing Metformin medication
    meds = await live_client.api_call(f"patient/{PATIENT_PID}/medication")
    assert isinstance(meds, list), f"Medication list failed: {meds}"
    metformin = next(
        (m for m in meds if "Metformin" in m.get("title", "")), None
    )
    assert metformin is not None, "Metformin not found in medication list"
    original_title = metformin["title"]
    original_id = metformin["id"]

    # Build a manifest with a dose-only update (no drug name in proposed_value)
    loop = AgentLoop(
        anthropic_client=anthropic.AsyncAnthropic(api_key="unused"),
        openemr_client=live_client,
    )
    session = AgentSession(
        fhir_patient_id=PATIENT_UUID,
        page_context=PageContext(patient_id=PATIENT_PID),
        manifest=ChangeManifest(
            patient_id=PATIENT_UUID,
            items=[
                ManifestItem(
                    resource_type="MedicationRequest",
                    action=ManifestAction.UPDATE,
                    target_resource_id=metformin["uuid"],
                    proposed_value={
                        "ref": f"MedicationRequest/{metformin['uuid']}",
                        "dose": "500mg twice daily",
                    },
                    source_reference="test",
                    description="Reduce metformin dose",
                    status="approved",
                ),
            ],
        ),
    )

    session = await loop.execute_approved(session)

    item = session.manifest.items[0]
    assert item.status == "completed", (
        f"Update failed: {item.status} — {item.execution_result}"
    )

    # Verify via the list endpoint (getOne 500s due to OpenEMR UUID
    # binary serialization bug, but getAll works fine)
    all_meds = await live_client.api_call(f"patient/{PATIENT_PID}/medication")
    assert isinstance(all_meds, list), f"List failed: {all_meds}"
    updated = next(
        (m for m in all_meds if m.get("id") == original_id), None
    )
    assert updated is not None, f"Medication {original_id} not found after update"

    assert "Metformin" in updated["title"] or \
           "metformin" in updated["title"].lower(), (
        f"Drug name lost! Title is now: {updated['title']}"
    )
    assert "500mg" in updated["title"], (
        f"Dose not updated. Title: {updated['title']}"
    )

    # Restore original title
    await live_client.api_call(
        f"patient/{PATIENT_PID}/medication/{original_id}",
        method="PUT",
        payload={"title": original_title, "begdate": updated.get("begdate")},
    )


async def test_medication_create_and_update_in_same_manifest(live_client):
    """A manifest with both create and update items executes correctly.

    Regression test for the cascade bug where REST POST creates medication
    records without UUIDs, causing the list endpoint to 500 and breaking
    subsequent update operations in the same manifest.
    """
    # Get an existing medication to update
    meds = await live_client.api_call(f"patient/{PATIENT_PID}/medication")
    assert isinstance(meds, list), f"Medication list failed: {meds}"
    apixaban = next(
        (m for m in meds if "Apixaban" in m.get("title", "")), None
    )
    assert apixaban is not None, "Apixaban not found in medication list"
    original_title = apixaban["title"]
    original_id = apixaban["id"]

    loop = AgentLoop(
        anthropic_client=anthropic.AsyncAnthropic(api_key="unused"),
        openemr_client=live_client,
    )
    session = AgentSession(
        fhir_patient_id=PATIENT_UUID,
        page_context=PageContext(patient_id=PATIENT_PID),
        manifest=ChangeManifest(
            patient_id=PATIENT_UUID,
            items=[
                # Create comes FIRST (this is the order the LLM produces)
                ManifestItem(
                    resource_type="MedicationRequest",
                    action=ManifestAction.CREATE,
                    proposed_value={
                        "type": "MedicationRequest",
                        "drug": "Semaglutide",
                        "dose": "0.5mg weekly",
                        "route": "subcutaneous",
                    },
                    source_reference="test",
                    description="Add semaglutide",
                    status="approved",
                ),
                # Update comes AFTER — must still work despite the
                # preceding create producing a null-UUID record
                ManifestItem(
                    resource_type="MedicationRequest",
                    action=ManifestAction.UPDATE,
                    target_resource_id=apixaban["uuid"],
                    proposed_value={
                        "ref": f"MedicationRequest/{apixaban['uuid']}",
                        "dose": "2.5mg twice daily",
                    },
                    source_reference="test",
                    description="Reduce apixaban dose",
                    status="approved",
                ),
            ],
        ),
    )

    session = await loop.execute_approved(session)

    create_item = session.manifest.items[0]
    update_item = session.manifest.items[1]

    assert create_item.status == "completed", (
        f"Create failed: {create_item.execution_result}"
    )
    assert update_item.status == "completed", (
        f"Update failed: {update_item.execution_result}"
    )

    # Clean up the null-UUID record from the create BEFORE verifying,
    # because OpenEMR's REST list endpoint 500s if any record has a
    # null UUID (the PID-based medication endpoint doesn't generate them).
    _db_query(
        f"DELETE FROM lists WHERE pid={PATIENT_PID} "
        "AND type='medication' AND uuid IS NULL"
    )

    # Verify the update preserved the drug name (use list endpoint;
    # getOne 500s due to OpenEMR UUID binary serialization bug)
    all_meds = await live_client.api_call(f"patient/{PATIENT_PID}/medication")
    assert isinstance(all_meds, list), f"List failed after execute: {all_meds}"
    updated = next(
        (m for m in all_meds if m.get("id") == original_id), None
    )
    assert updated is not None, f"Medication {original_id} not found after update"

    assert "Apixaban" in updated["title"] or \
           "apixaban" in updated["title"].lower(), (
        f"Drug name lost! Title is now: {updated['title']}"
    )
    assert "2.5mg" in updated["title"], (
        f"Dose not updated. Title: {updated['title']}"
    )

    # Clean up: restore original apixaban title, delete semaglutide
    await live_client.api_call(
        f"patient/{PATIENT_PID}/medication/{original_id}",
        method="PUT",
        payload={"title": original_title, "begdate": updated.get("begdate")},
    )
    # Find and delete the semaglutide record (and clean null-UUID records)
    _db_query(
        f"DELETE FROM lists WHERE pid={PATIENT_PID} "
        "AND type='medication' AND title LIKE '%Semaglutide%'"
    )
    _db_query(
        f"DELETE FROM lists WHERE pid={PATIENT_PID} "
        "AND type='medication' AND uuid IS NULL"
    )
