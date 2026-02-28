"""Happy-path tests for execute_approved() in AgentLoop.

This is the most dangerous code path — it writes to patient medical records.
Tests cover CREATE, UPDATE, and DELETE for Condition (UUID-based endpoint)
and MedicationRequest (PID-based endpoint with list-ID resolution).

Ref: thread T-019ca504-24c8-73fc-88be-b06bc908de07
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from src.agent.loop import AgentLoop
from src.agent.models import (
    AgentSession,
    ChangeManifest,
    ManifestAction,
    ManifestItem,
    PageContext,
)

PATIENT_FHIR_UUID = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
PATIENT_PID = "5"
ENCOUNTER_FHIR_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CONDITION_FHIR_UUID = "cccccccc-1111-2222-3333-444444444444"
MED_FHIR_UUID = "dddddddd-1111-2222-3333-444444444444"
TODAY = date.today().isoformat() + " 00:00:00"


def _make_loop(openemr_client: AsyncMock) -> AgentLoop:
    anthropic_client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(
            content=[], stop_reason="end_turn", usage={},
        )))
    )
    return AgentLoop(anthropic_client=anthropic_client, openemr_client=openemr_client)


def _make_session(**manifest_overrides) -> AgentSession:
    session = AgentSession()
    session.fhir_patient_id = PATIENT_FHIR_UUID
    session.page_context = PageContext(patient_id=PATIENT_PID)
    manifest_defaults = dict(patient_id=PATIENT_FHIR_UUID)
    manifest_defaults.update(manifest_overrides)
    session.manifest = ChangeManifest(**manifest_defaults)
    return session


# ==================================================================
# CREATE happy paths
# ==================================================================

class TestCreateCondition:

    @pytest.mark.asyncio
    async def test_create_condition_posts_to_uuid_endpoint(self):
        """Condition CREATE uses patient UUID (not PID) in the endpoint."""
        openemr_client = AsyncMock()
        # Pre-cache call returns empty list (no medications to cache)
        # Then the POST returns success
        openemr_client.api_call.side_effect = [
            [],  # pre-cache: GET patient/5/medication
            {"uuid": "new-uuid", "id": 99},  # POST result
        ]
        loop = _make_loop(openemr_client)
        session = _make_session(items=[
            ManifestItem(
                id="cond-create-1",
                resource_type="Condition",
                action=ManifestAction.CREATE,
                proposed_value={"code": "E11.9", "display": "Type 2 diabetes mellitus"},
                source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                description="Add diabetes diagnosis",
                status="approved",
            ),
        ])

        session = await loop.execute_approved(session)

        item = session.manifest.items[0]
        assert item.status == "completed"
        assert session.phase == "complete"
        assert session.manifest.status == "completed"

        # Verify the POST call (second api_call, after pre-cache)
        post_call = openemr_client.api_call.call_args_list[1]
        assert post_call.kwargs["endpoint"] == f"patient/{PATIENT_FHIR_UUID}/medical_problem"
        assert post_call.kwargs["method"] == "POST"
        payload = post_call.kwargs["payload"]
        assert payload["title"] == "Type 2 diabetes mellitus"
        assert payload["diagnosis"] == "ICD10:E11.9"
        assert payload["begdate"] == TODAY


class TestCreateMedicationRequest:

    @pytest.mark.asyncio
    async def test_create_medication_posts_to_pid_endpoint(self):
        """MedicationRequest CREATE uses numeric PID (not UUID) in the endpoint."""
        openemr_client = AsyncMock()
        openemr_client.api_call.side_effect = [
            [],  # pre-cache: GET patient/5/medication
            {"uuid": "new-med-uuid", "id": 42},  # POST result
        ]
        loop = _make_loop(openemr_client)
        session = _make_session(items=[
            ManifestItem(
                id="med-create-1",
                resource_type="MedicationRequest",
                action=ManifestAction.CREATE,
                proposed_value={"drug": "Metformin", "dose": "500mg", "route": "oral"},
                source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                description="Start metformin for diabetes",
                status="approved",
            ),
        ])

        session = await loop.execute_approved(session)

        item = session.manifest.items[0]
        assert item.status == "completed"

        post_call = openemr_client.api_call.call_args_list[1]
        assert post_call.kwargs["endpoint"] == f"patient/{PATIENT_PID}/medication"
        assert post_call.kwargs["method"] == "POST"
        payload = post_call.kwargs["payload"]
        assert payload["title"] == "Metformin 500mg oral"
        assert payload["begdate"] == TODAY
        assert payload["comments"] == "Start metformin for diabetes"


# ==================================================================
# UPDATE happy paths
# ==================================================================

class TestUpdateCondition:

    @pytest.mark.asyncio
    async def test_update_condition_puts_to_uuid_endpoint(self):
        """Condition UPDATE uses FHIR UUID as both endpoint and resource ID."""
        openemr_client = AsyncMock()
        openemr_client.api_call.side_effect = [
            [],  # pre-cache
            {"status": "ok"},  # PUT result
        ]
        loop = _make_loop(openemr_client)
        session = _make_session(items=[
            ManifestItem(
                id="cond-update-1",
                resource_type="Condition",
                action=ManifestAction.UPDATE,
                proposed_value={
                    "ref": f"Condition/{CONDITION_FHIR_UUID}",
                    "code": "I10",
                    "display": "Essential hypertension",
                },
                target_resource_id=CONDITION_FHIR_UUID,
                source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                description="Update diagnosis code",
                status="approved",
            ),
        ])

        session = await loop.execute_approved(session)

        item = session.manifest.items[0]
        assert item.status == "completed"

        put_call = openemr_client.api_call.call_args_list[1]
        assert put_call.kwargs["endpoint"] == (
            f"patient/{PATIENT_FHIR_UUID}/medical_problem/{CONDITION_FHIR_UUID}"
        )
        assert put_call.kwargs["method"] == "PUT"
        payload = put_call.kwargs["payload"]
        assert payload["title"] == "Essential hypertension"
        assert payload["diagnosis"] == "ICD10:I10"


class TestUpdateMedicationRequest:

    @pytest.mark.asyncio
    async def test_update_medication_resolves_list_id_and_merges(self):
        """MedicationRequest UPDATE resolves FHIR UUID to numeric list ID
        from pre-cached records and merges proposed changes onto cached record."""
        openemr_client = AsyncMock()
        cached_med = {
            "id": 7,
            "uuid": MED_FHIR_UUID,
            "pid": 5,
            "title": "Metformin 500mg oral",
            "begdate": "2024-06-01 00:00:00",
            "enddate": None,
            "comments": "Initial prescription",
        }
        openemr_client.api_call.side_effect = [
            [cached_med],  # pre-cache: GET patient/5/medication
            {"status": "ok"},  # PUT result
        ]
        loop = _make_loop(openemr_client)
        session = _make_session(items=[
            ManifestItem(
                id="med-update-1",
                resource_type="MedicationRequest",
                action=ManifestAction.UPDATE,
                proposed_value={
                    "ref": f"MedicationRequest/{MED_FHIR_UUID}",
                    "dose": "1000mg",
                },
                target_resource_id=MED_FHIR_UUID,
                source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                description="Increase metformin dose",
                status="approved",
            ),
        ])

        session = await loop.execute_approved(session)

        item = session.manifest.items[0]
        assert item.status == "completed"

        put_call = openemr_client.api_call.call_args_list[1]
        assert put_call.kwargs["endpoint"] == f"patient/{PATIENT_PID}/medication/7"
        assert put_call.kwargs["method"] == "PUT"
        payload = put_call.kwargs["payload"]
        # Title should be rebuilt: existing drug name + new dose
        assert payload["title"] == "Metformin 1000mg"
        # Merged fields from cached record should be preserved
        assert payload["begdate"] == "2024-06-01 00:00:00"
        assert payload["comments"] == "Increase metformin dose"

    @pytest.mark.asyncio
    async def test_update_medication_with_drug_name_uses_provided_title(self):
        """When proposed_value includes a drug name, title comes from translator
        (not the rebuild logic)."""
        openemr_client = AsyncMock()
        cached_med = {
            "id": 7,
            "uuid": MED_FHIR_UUID,
            "pid": 5,
            "title": "Metformin 500mg",
            "begdate": "2024-06-01 00:00:00",
            "enddate": None,
            "comments": "old",
        }
        openemr_client.api_call.side_effect = [
            [cached_med],  # pre-cache
            {"status": "ok"},  # PUT result
        ]
        loop = _make_loop(openemr_client)
        session = _make_session(items=[
            ManifestItem(
                id="med-update-2",
                resource_type="MedicationRequest",
                action=ManifestAction.UPDATE,
                proposed_value={
                    "ref": f"MedicationRequest/{MED_FHIR_UUID}",
                    "drug": "Metformin XR",
                    "dose": "750mg",
                },
                target_resource_id=MED_FHIR_UUID,
                source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                description="Switch to extended release",
                status="approved",
            ),
        ])

        session = await loop.execute_approved(session)

        item = session.manifest.items[0]
        assert item.status == "completed"

        put_call = openemr_client.api_call.call_args_list[1]
        payload = put_call.kwargs["payload"]
        assert payload["title"] == "Metformin XR 750mg"


# ==================================================================
# DELETE happy paths
# ==================================================================

class TestDeleteCondition:

    @pytest.mark.asyncio
    async def test_delete_condition_uses_uuid_endpoint(self):
        """Condition DELETE uses FHIR UUID directly (no list-ID resolution)."""
        openemr_client = AsyncMock()
        openemr_client.api_call.side_effect = [
            [],  # pre-cache
            {"status": "ok"},  # DELETE result
        ]
        loop = _make_loop(openemr_client)
        session = _make_session(items=[
            ManifestItem(
                id="cond-delete-1",
                resource_type="Condition",
                action=ManifestAction.DELETE,
                proposed_value={"ref": f"Condition/{CONDITION_FHIR_UUID}"},
                target_resource_id=CONDITION_FHIR_UUID,
                source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                description="Remove resolved condition",
                status="approved",
            ),
        ])

        session = await loop.execute_approved(session)

        item = session.manifest.items[0]
        assert item.status == "completed"

        delete_call = openemr_client.api_call.call_args_list[1]
        assert delete_call.kwargs["endpoint"] == (
            f"patient/{PATIENT_FHIR_UUID}/medical_problem/{CONDITION_FHIR_UUID}"
        )
        assert delete_call.kwargs["method"] == "DELETE"


class TestDeleteMedicationRequest:

    @pytest.mark.asyncio
    async def test_delete_medication_resolves_list_id(self):
        """MedicationRequest DELETE resolves FHIR UUID → numeric list ID
        via _resolve_list_id before issuing the DELETE call."""
        openemr_client = AsyncMock()
        openemr_client.api_call.side_effect = [
            # pre-cache: GET patient/5/medication (returns existing meds)
            [{"id": 7, "uuid": MED_FHIR_UUID, "title": "Metformin 500mg"}],
            # _resolve_list_id: GET patient/5/medication (fetches list again)
            [{"id": 7, "uuid": MED_FHIR_UUID, "title": "Metformin 500mg"}],
            # DELETE result
            {"status": "ok"},
        ]
        loop = _make_loop(openemr_client)
        session = _make_session(items=[
            ManifestItem(
                id="med-delete-1",
                resource_type="MedicationRequest",
                action=ManifestAction.DELETE,
                proposed_value={"ref": f"MedicationRequest/{MED_FHIR_UUID}"},
                target_resource_id=MED_FHIR_UUID,
                source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                description="Discontinue medication",
                status="approved",
            ),
        ])

        session = await loop.execute_approved(session)

        item = session.manifest.items[0]
        assert item.status == "completed"

        delete_call = openemr_client.api_call.call_args_list[2]
        assert delete_call.kwargs["endpoint"] == f"patient/{PATIENT_PID}/medication/7"
        assert delete_call.kwargs["method"] == "DELETE"


# ==================================================================
# Session state transitions
# ==================================================================

class TestSessionStateAfterExecution:

    @pytest.mark.asyncio
    async def test_all_succeeded_sets_completed_status(self):
        openemr_client = AsyncMock()
        openemr_client.api_call.side_effect = [
            [],  # pre-cache
            {"id": 1},  # POST result
        ]
        loop = _make_loop(openemr_client)
        session = _make_session(items=[
            ManifestItem(
                id="s1",
                resource_type="Condition",
                action=ManifestAction.CREATE,
                proposed_value={"code": "E11.9", "display": "Diabetes"},
                source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                description="Add diabetes",
                status="approved",
            ),
        ])

        session = await loop.execute_approved(session)

        assert session.phase == "complete"
        assert session.manifest.status == "completed"
        assert "1 succeeded" in session.messages[-1].content
        assert "0 failed" in session.messages[-1].content

    @pytest.mark.asyncio
    async def test_mixed_results_sets_failed_status(self):
        """If any item fails, manifest status should be 'failed'."""
        openemr_client = AsyncMock()
        openemr_client.api_call.side_effect = [
            [],  # pre-cache
            {"id": 1},  # first POST succeeds
            Exception("server error"),  # second POST fails
        ]
        loop = _make_loop(openemr_client)
        session = _make_session(items=[
            ManifestItem(
                id="ok-1",
                resource_type="Condition",
                action=ManifestAction.CREATE,
                proposed_value={"code": "E11.9", "display": "Diabetes"},
                source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                description="Good item",
                status="approved",
            ),
            ManifestItem(
                id="bad-1",
                resource_type="Condition",
                action=ManifestAction.CREATE,
                proposed_value={"code": "I10", "display": "Hypertension"},
                source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                description="Bad item",
                status="approved",
            ),
        ])

        session = await loop.execute_approved(session)

        assert session.manifest.status == "failed"
        assert session.manifest.items[0].status == "completed"
        assert session.manifest.items[1].status == "failed"
        assert "1 succeeded" in session.messages[-1].content
        assert "1 failed" in session.messages[-1].content

    @pytest.mark.asyncio
    async def test_dependency_skip_cascades(self):
        """If a dependency fails, dependent items should be skipped."""
        openemr_client = AsyncMock()
        openemr_client.api_call.side_effect = [
            [],  # pre-cache
            Exception("server error"),  # first item fails
        ]
        loop = _make_loop(openemr_client)
        session = _make_session(items=[
            ManifestItem(
                id="parent-1",
                resource_type="Condition",
                action=ManifestAction.CREATE,
                proposed_value={"code": "E11.9", "display": "Diabetes"},
                source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                description="Parent item",
                status="approved",
            ),
            ManifestItem(
                id="child-1",
                resource_type="Condition",
                action=ManifestAction.CREATE,
                proposed_value={"code": "E11.65", "display": "Diabetes with hyperglycemia"},
                source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                description="Depends on parent",
                status="approved",
                depends_on=["parent-1"],
            ),
        ])

        session = await loop.execute_approved(session)

        assert session.manifest.items[0].status == "failed"
        assert session.manifest.items[1].status == "skipped"
        assert session.manifest.items[1].execution_result == "Dependency failed"
        assert "1 skipped" in session.messages[-1].content
