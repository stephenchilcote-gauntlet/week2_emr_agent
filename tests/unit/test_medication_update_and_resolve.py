"""Tests for medication UPDATE title-merge logic and _resolve_list_id().

Covers the regex-based title reconstruction in execute_approved() (loop.py
lines 506-528) and the UUID→numeric-ID resolution in _resolve_list_id().

Thread: T-019ca504-24c8-73fc-88be-b06bc908de07
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

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
ENCOUNTER_FHIR_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
MED_FHIR_UUID = "dddddddd-1111-2222-3333-444444444444"


def _make_loop(openemr_client: AsyncMock) -> AgentLoop:
    anthropic_client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(
            content=[], stop_reason="end_turn", usage={},
        )))
    )
    return AgentLoop(anthropic_client=anthropic_client, openemr_client=openemr_client)


def _make_session_with_med_update(
    existing_title: str,
    proposed_value: dict,
    *,
    cached_record: dict | None = None,
) -> tuple[AgentSession, AsyncMock]:
    """Build an AgentSession with a single MedicationRequest UPDATE item
    and a mock openemr_client whose api_call returns the cached medication
    list for the pre-fetch, then accepts the PUT.
    """
    cached = cached_record or {
        "id": 42,
        "uuid": MED_FHIR_UUID,
        "pid": 5,
        "title": existing_title,
        "begdate": "2025-01-01 00:00:00",
        "enddate": None,
        "comments": "existing comment",
    }

    openemr_client = AsyncMock()
    # First api_call = pre-fetch medication list; second = the PUT write.
    openemr_client.api_call.side_effect = [
        [cached],                  # pre-fetch: list of medication records
        {"status": "ok"},          # PUT result
    ]

    session = AgentSession()
    session.fhir_patient_id = PATIENT_FHIR_UUID
    session.page_context = PageContext(patient_id="5")
    session.manifest = ChangeManifest(
        patient_id=PATIENT_FHIR_UUID,
        encounter_id=ENCOUNTER_FHIR_UUID,
        items=[
            ManifestItem(
                id="med-1",
                resource_type="MedicationRequest",
                action=ManifestAction.UPDATE,
                proposed_value=proposed_value,
                target_resource_id=MED_FHIR_UUID,
                source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                description="Update medication",
                status="approved",
            ),
        ],
    )
    return session, openemr_client


# ==================================================================
# 1. Medication UPDATE title-merge logic
# ==================================================================

class TestMedicationTitleMerge:
    """Test the regex-based title reconstruction when dose/route changes
    are applied without an explicit drug name."""

    @pytest.mark.asyncio
    async def test_dose_change_preserves_drug_name(self):
        """Existing 'Metformin 500mg oral', edit dose to '1000mg'
        → new title 'Metformin 1000mg'."""
        session, client = _make_session_with_med_update(
            existing_title="Metformin 500mg oral",
            proposed_value={"ref": f"MedicationRequest/{MED_FHIR_UUID}", "dose": "1000mg"},
        )
        loop = _make_loop(client)

        manifest_items = session.manifest.items

        session = await loop.execute_approved(session)

        item = manifest_items[0]
        assert item.status == "completed", f"Expected completed, got {item.status}: {item.execution_result}"

        # Verify the PUT payload had the merged title
        put_call = client.api_call.call_args_list[1]
        put_payload = put_call.kwargs.get("payload") or put_call[1].get("payload")
        assert put_payload["title"] == "Metformin 1000mg"

    @pytest.mark.asyncio
    async def test_add_route_to_drug_without_dose(self):
        """Existing 'Aspirin', add route 'oral' → 'Aspirin oral'."""
        session, client = _make_session_with_med_update(
            existing_title="Aspirin",
            proposed_value={"ref": f"MedicationRequest/{MED_FHIR_UUID}", "route": "oral"},
        )
        loop = _make_loop(client)

        manifest_items = session.manifest.items

        session = await loop.execute_approved(session)

        item = manifest_items[0]
        assert item.status == "completed", f"Expected completed, got {item.status}: {item.execution_result}"

        put_call = client.api_call.call_args_list[1]
        put_payload = put_call.kwargs.get("payload") or put_call[1].get("payload")
        assert put_payload["title"] == "Aspirin oral"

    @pytest.mark.asyncio
    async def test_complex_drug_name_preserved(self):
        """Existing 'Amoxicillin/Clavulanate 875mg', edit dose to '500mg'
        → preserves compound drug name as 'Amoxicillin/Clavulanate 500mg'."""
        session, client = _make_session_with_med_update(
            existing_title="Amoxicillin/Clavulanate 875mg",
            proposed_value={"ref": f"MedicationRequest/{MED_FHIR_UUID}", "dose": "500mg"},
        )
        loop = _make_loop(client)

        manifest_items = session.manifest.items

        session = await loop.execute_approved(session)

        item = manifest_items[0]
        assert item.status == "completed", f"Expected completed, got {item.status}: {item.execution_result}"

        put_call = client.api_call.call_args_list[1]
        put_payload = put_call.kwargs.get("payload") or put_call[1].get("payload")
        assert put_payload["title"] == "Amoxicillin/Clavulanate 500mg"

    @pytest.mark.asyncio
    async def test_dose_and_route_both_changed(self):
        """Existing 'Lisinopril 10mg', edit dose='20mg' route='oral'
        → 'Lisinopril 20mg oral'."""
        session, client = _make_session_with_med_update(
            existing_title="Lisinopril 10mg",
            proposed_value={
                "ref": f"MedicationRequest/{MED_FHIR_UUID}",
                "dose": "20mg",
                "route": "oral",
            },
        )
        loop = _make_loop(client)

        manifest_items = session.manifest.items

        session = await loop.execute_approved(session)

        item = manifest_items[0]
        assert item.status == "completed"

        put_call = client.api_call.call_args_list[1]
        put_payload = put_call.kwargs.get("payload") or put_call[1].get("payload")
        assert put_payload["title"] == "Lisinopril 20mg oral"

    @pytest.mark.asyncio
    async def test_explicit_drug_name_skips_merge(self):
        """When the proposed_value includes a drug name, the translator
        builds the title directly — the merge regex should NOT run."""
        session, client = _make_session_with_med_update(
            existing_title="Metformin 500mg",
            proposed_value={
                "ref": f"MedicationRequest/{MED_FHIR_UUID}",
                "drug": "Metformin",
                "dose": "1000mg",
            },
        )
        loop = _make_loop(client)

        manifest_items = session.manifest.items

        session = await loop.execute_approved(session)

        item = manifest_items[0]
        assert item.status == "completed"

        put_call = client.api_call.call_args_list[1]
        put_payload = put_call.kwargs.get("payload") or put_call[1].get("payload")
        # Translator builds title="Metformin 1000mg" directly, merged onto cached
        assert "Metformin" in put_payload["title"]
        assert "1000mg" in put_payload["title"]

    @pytest.mark.asyncio
    async def test_hyphenated_drug_name_preserved(self):
        """Drug names with hyphens like 'Co-Amoxiclav 625mg' should be preserved."""
        session, client = _make_session_with_med_update(
            existing_title="Co-Amoxiclav 625mg",
            proposed_value={"ref": f"MedicationRequest/{MED_FHIR_UUID}", "dose": "1000mg"},
        )
        loop = _make_loop(client)

        manifest_items = session.manifest.items

        session = await loop.execute_approved(session)

        item = manifest_items[0]
        assert item.status == "completed"

        put_call = client.api_call.call_args_list[1]
        put_payload = put_call.kwargs.get("payload") or put_call[1].get("payload")
        assert put_payload["title"] == "Co-Amoxiclav 1000mg"


# ==================================================================
# 2. _resolve_list_id()
# ==================================================================

class TestResolveListId:
    """Test the UUID → numeric list ID resolution."""

    @pytest.mark.asyncio
    async def test_resolves_matching_uuid(self):
        """Returns the numeric ID when the UUID is found in the list."""
        openemr_client = AsyncMock()
        openemr_client.api_call.return_value = [
            {"id": 10, "uuid": "aaa-111"},
            {"id": 42, "uuid": MED_FHIR_UUID},
            {"id": 99, "uuid": "ccc-333"},
        ]
        loop = _make_loop(openemr_client)

        result = await loop._resolve_list_id("patient/5/medication", MED_FHIR_UUID)

        assert result == "42"

    @pytest.mark.asyncio
    async def test_raises_when_uuid_not_found(self):
        """Raises ValueError when the UUID is not in the list."""
        openemr_client = AsyncMock()
        openemr_client.api_call.return_value = [
            {"id": 10, "uuid": "aaa-111"},
            {"id": 99, "uuid": "ccc-333"},
        ]
        loop = _make_loop(openemr_client)

        with pytest.raises(ValueError, match="not found in REST endpoint"):
            await loop._resolve_list_id("patient/5/medication", MED_FHIR_UUID)

    @pytest.mark.asyncio
    async def test_raises_when_api_returns_non_list(self):
        """Raises ValueError when api_call returns a dict instead of a list."""
        openemr_client = AsyncMock()
        openemr_client.api_call.return_value = {"error": "not found"}
        loop = _make_loop(openemr_client)

        with pytest.raises(ValueError, match="not found in REST endpoint"):
            await loop._resolve_list_id("patient/5/medication", MED_FHIR_UUID)

    @pytest.mark.asyncio
    async def test_raises_when_entries_have_null_uuids(self):
        """Entries with null UUIDs (from REST POST bug) should not match."""
        openemr_client = AsyncMock()
        openemr_client.api_call.return_value = [
            {"id": 10, "uuid": None},
            {"id": 42, "uuid": None},
        ]
        loop = _make_loop(openemr_client)

        with pytest.raises(ValueError, match="not found in REST endpoint"):
            await loop._resolve_list_id("patient/5/medication", MED_FHIR_UUID)

    @pytest.mark.asyncio
    async def test_empty_list_raises(self):
        """Empty list from API should raise ValueError."""
        openemr_client = AsyncMock()
        openemr_client.api_call.return_value = []
        loop = _make_loop(openemr_client)

        with pytest.raises(ValueError, match="not found in REST endpoint"):
            await loop._resolve_list_id("patient/5/medication", MED_FHIR_UUID)
