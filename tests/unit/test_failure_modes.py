"""Failure-mode tests: verify the system rejects invalid inputs and handles errors.

These tests cover the negative paths that the happy-path-only test suite missed,
causing bugs like "FHIR write to read-only endpoint" and "unsupported resource
types (DocumentReference, ServiceRequest, Observation)" to escape to production.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agent.dsl import DslItem
from src.agent.loop import AgentLoop
from src.agent.models import (
    AgentSession,
    ChangeManifest,
    ManifestAction,
    ManifestItem,
    PageContext,
    ToolCall,
)
from src.agent.translator import (
    can_rest_write,
    get_rest_endpoint,
    to_openemr_rest,
)
from src.verification.checks import (
    check_confidence,
    check_constraints,
    check_dose_sanity,
    check_grounding,
    check_medication_safety,
    verify_manifest,
)

PATIENT_FHIR_UUID = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
ENCOUNTER_FHIR_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _make_item(**overrides) -> DslItem:
    defaults = dict(
        action="add",
        resource_type="Condition",
        description="test item",
        source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
        item_id="item-1",
        confidence="high",
        depends_on=[],
        ref=None,
        attrs={},
    )
    defaults.update(overrides)
    return DslItem(**defaults)


def _make_manifest_item(**overrides) -> ManifestItem:
    defaults = dict(
        resource_type="Condition",
        action=ManifestAction.CREATE,
        proposed_value={"code": "E11.9"},
        source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
        description="Test item",
    )
    defaults.update(overrides)
    return ManifestItem(**defaults)


def _make_loop(openemr_client: AsyncMock) -> AgentLoop:
    anthropic_client = SimpleNamespace(
        messages=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(
            content=[], stop_reason="end_turn", usage={},
        )))
    )
    return AgentLoop(anthropic_client=anthropic_client, openemr_client=openemr_client)


# ==================================================================
# 1. Translator rejects unsupported resource types
# ==================================================================

class TestTranslatorRejectsUnsupportedTypes:
    """Resource types that caused rework when the agent tried to write them."""

    def test_document_reference_raises(self):
        item = _make_item(resource_type="DocumentReference")
        with pytest.raises(ValueError, match="No OpenEMR REST builder"):
            to_openemr_rest(item, PATIENT_FHIR_UUID)

    def test_service_request_raises(self):
        item = _make_item(resource_type="ServiceRequest")
        with pytest.raises(ValueError, match="No OpenEMR REST builder"):
            to_openemr_rest(item, PATIENT_FHIR_UUID)

    def test_observation_raises(self):
        item = _make_item(resource_type="Observation")
        with pytest.raises(ValueError, match="No OpenEMR REST builder"):
            to_openemr_rest(item, PATIENT_FHIR_UUID)

    def test_can_rest_write_false_for_unsupported(self):
        for rtype in ("DocumentReference", "ServiceRequest", "Observation", "CarePlan"):
            assert can_rest_write(rtype) is False, f"{rtype} should not be writable"

    def test_get_rest_endpoint_rejects_document_reference(self):
        item = _make_item(resource_type="DocumentReference")
        with pytest.raises(ValueError, match="No REST endpoint"):
            get_rest_endpoint(item, PATIENT_FHIR_UUID)

    def test_get_rest_endpoint_rejects_service_request(self):
        item = _make_item(resource_type="ServiceRequest")
        with pytest.raises(ValueError, match="No REST endpoint"):
            get_rest_endpoint(item, PATIENT_FHIR_UUID)

    def test_get_rest_endpoint_rejects_observation(self):
        item = _make_item(resource_type="Observation")
        with pytest.raises(ValueError, match="No REST endpoint"):
            get_rest_endpoint(item, PATIENT_FHIR_UUID)

    def test_encounter_scoped_endpoint_requires_encounter_id(self):
        item = _make_item(resource_type="SoapNote")
        with pytest.raises(ValueError, match="requires an encounter_id"):
            get_rest_endpoint(item, PATIENT_FHIR_UUID)


# ==================================================================
# 2. Agent loop handles tool execution errors without crashing
# ==================================================================

class TestAgentLoopToolErrors:

    @pytest.mark.asyncio
    async def test_fhir_read_http_error_returns_is_error(self):
        openemr_client = AsyncMock()
        openemr_client.fhir_read.side_effect = Exception("401 Unauthorized")
        loop = _make_loop(openemr_client)
        session = AgentSession()

        result = await loop._execute_tool(
            ToolCall(id="t1", name="fhir_read", arguments={
                "resource_type": "Patient", "params": {"_id": "bad"},
            }),
            session,
        )

        assert result.is_error is True
        assert "401" in result.content or "Error" in result.content

    @pytest.mark.asyncio
    async def test_api_call_error_propagates(self):
        openemr_client = AsyncMock()
        openemr_client.api_call.side_effect = ConnectionError("refused")
        loop = _make_loop(openemr_client)
        session = AgentSession()

        result = await loop._execute_tool(
            ToolCall(id="t2", name="openemr_api", arguments={
                "endpoint": "patient/1/medication",
            }),
            session,
        )

        assert result.is_error is True
        assert "refused" in result.content

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_is_error(self):
        openemr_client = AsyncMock()
        loop = _make_loop(openemr_client)
        session = AgentSession()

        result = await loop._execute_tool(
            ToolCall(id="t3", name="nonexistent_tool", arguments={}),
            session,
        )

        assert result.is_error is True
        assert "unknown tool" in result.content

    @pytest.mark.asyncio
    async def test_submit_manifest_during_review_is_error(self):
        openemr_client = AsyncMock()
        loop = _make_loop(openemr_client)
        session = AgentSession()
        session.phase = "reviewing"

        result = await loop._execute_tool(
            ToolCall(id="t4", name="submit_manifest", arguments={
                "items": [],
            }),
            session,
        )

        assert result.is_error is True
        assert "already in reviewing" in result.content


# ==================================================================
# 3. Manifest validation rejects malformed manifests
# ==================================================================

class TestManifestValidation:

    @pytest.mark.asyncio
    async def test_execute_approved_with_no_manifest_raises(self):
        openemr_client = AsyncMock()
        loop = _make_loop(openemr_client)
        session = AgentSession()
        session.manifest = None

        with pytest.raises(ValueError, match="No manifest"):
            await loop.execute_approved(session)

    @pytest.mark.asyncio
    async def test_execute_unwritable_resource_type_fails(self):
        """Items with resource types that can_rest_write() returns False for
        should fail at execution, not silently succeed."""
        openemr_client = AsyncMock()
        openemr_client.api_call.return_value = []
        loop = _make_loop(openemr_client)
        session = AgentSession()
        session.fhir_patient_id = PATIENT_FHIR_UUID
        session.page_context = PageContext(patient_id="5")
        session.manifest = ChangeManifest(
            patient_id=PATIENT_FHIR_UUID,
            items=[
                ManifestItem(
                    id="obs-1",
                    resource_type="Observation",
                    action=ManifestAction.CREATE,
                    proposed_value={"value": "120"},
                    source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                    description="Blood pressure",
                    status="approved",
                ),
            ],
        )

        manifest_items = session.manifest.items

        session = await loop.execute_approved(session)

        item = manifest_items[0]
        assert item.status == "failed"
        assert "No REST write path" in item.execution_result

    @pytest.mark.asyncio
    async def test_execute_update_without_target_id_fails(self):
        """Update actions without a target_resource_id or ref should fail."""
        openemr_client = AsyncMock()
        openemr_client.api_call.return_value = []
        loop = _make_loop(openemr_client)
        session = AgentSession()
        session.fhir_patient_id = PATIENT_FHIR_UUID
        session.page_context = PageContext(patient_id="5")
        session.manifest = ChangeManifest(
            patient_id=PATIENT_FHIR_UUID,
            items=[
                ManifestItem(
                    id="cond-1",
                    resource_type="Condition",
                    action=ManifestAction.UPDATE,
                    proposed_value={"code": "I10"},
                    target_resource_id=None,
                    source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                    description="Update condition",
                    status="approved",
                ),
            ],
        )

        manifest_items = session.manifest.items

        session = await loop.execute_approved(session)

        item = manifest_items[0]
        assert item.status == "failed"
        assert "no target resource ID" in item.execution_result

    @pytest.mark.asyncio
    async def test_execute_delete_without_target_id_fails(self):
        openemr_client = AsyncMock()
        openemr_client.api_call.return_value = []
        loop = _make_loop(openemr_client)
        session = AgentSession()
        session.fhir_patient_id = PATIENT_FHIR_UUID
        session.page_context = PageContext(patient_id="5")
        session.manifest = ChangeManifest(
            patient_id=PATIENT_FHIR_UUID,
            items=[
                ManifestItem(
                    id="cond-2",
                    resource_type="Condition",
                    action=ManifestAction.DELETE,
                    proposed_value={},
                    target_resource_id=None,
                    source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                    description="Delete condition",
                    status="approved",
                ),
            ],
        )

        manifest_items = session.manifest.items

        session = await loop.execute_approved(session)

        item = manifest_items[0]
        assert item.status == "failed"
        assert "no target resource ID" in item.execution_result

    @pytest.mark.asyncio
    async def test_execute_skips_unapproved_items(self):
        openemr_client = AsyncMock()
        openemr_client.api_call.return_value = []
        loop = _make_loop(openemr_client)
        session = AgentSession()
        session.fhir_patient_id = PATIENT_FHIR_UUID
        session.page_context = PageContext(patient_id="5")
        session.manifest = ChangeManifest(
            patient_id=PATIENT_FHIR_UUID,
            items=[
                ManifestItem(
                    id="pending-1",
                    resource_type="Condition",
                    action=ManifestAction.CREATE,
                    proposed_value={"code": "E11.9"},
                    source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                    description="Pending item",
                    status="pending",
                ),
                ManifestItem(
                    id="rejected-1",
                    resource_type="Condition",
                    action=ManifestAction.CREATE,
                    proposed_value={"code": "I10"},
                    source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
                    description="Rejected item",
                    status="rejected",
                ),
            ],
        )

        manifest_items = session.manifest.items

        session = await loop.execute_approved(session)

        # No POST/PUT/DELETE writes should have been made.
        # (api_call may be called once for pre-caching medication list IDs,
        # but no write calls should happen for unapproved items.)
        for call_args in openemr_client.api_call.call_args_list:
            if len(call_args.kwargs) > 0:
                assert call_args.kwargs.get("method") not in ("POST", "PUT", "DELETE"), \
                    f"Unexpected write call: {call_args}"


# ==================================================================
# 4. API endpoint validation
# ==================================================================

class TestAPIValidation:

    def test_execute_manifest_no_manifest_returns_400(self):
        from fastapi.testclient import TestClient
        from src.api.main import app

        with TestClient(app) as client:
            client.app.state.agent_loop = SimpleNamespace()
            created = client.post(
                "/api/sessions", headers={"openemr_user_id": "u-fail"},
            ).json()

            resp = client.post(
                f"/api/manifest/{created['session_id']}/execute",
                headers={"openemr_user_id": "u-fail"},
            )

        assert resp.status_code == 400
        assert "No manifest" in resp.json()["detail"]

    def test_approve_nonexistent_session_returns_404(self):
        from fastapi.testclient import TestClient
        from src.api.main import app

        with TestClient(app) as client:
            client.app.state.agent_loop = SimpleNamespace()
            resp = client.post(
                "/api/manifest/nonexistent-id/approve",
                headers={"openemr_user_id": "u-fail"},
                json={"approved_items": [], "rejected_items": []},
            )

        assert resp.status_code == 404

    def test_execute_nonexistent_session_returns_404(self):
        from fastapi.testclient import TestClient
        from src.api.main import app

        with TestClient(app) as client:
            client.app.state.agent_loop = SimpleNamespace()
            resp = client.post(
                "/api/manifest/nonexistent-id/execute",
                headers={"openemr_user_id": "u-fail"},
            )

        assert resp.status_code == 404

    def test_approve_manifest_no_manifest_returns_400(self):
        from fastapi.testclient import TestClient
        from src.api.main import app

        with TestClient(app) as client:
            client.app.state.agent_loop = SimpleNamespace()
            created = client.post(
                "/api/sessions", headers={"openemr_user_id": "u-fail2"},
            ).json()

            resp = client.post(
                f"/api/manifest/{created['session_id']}/approve",
                headers={"openemr_user_id": "u-fail2"},
                json={"approved_items": [], "rejected_items": []},
            )

        assert resp.status_code == 400
        assert "No manifest" in resp.json()["detail"]

    def test_get_messages_wrong_user_returns_404(self):
        from fastapi.testclient import TestClient
        from src.api.main import app

        with TestClient(app) as client:
            client.app.state.agent_loop = SimpleNamespace()
            created = client.post(
                "/api/sessions", headers={"openemr_user_id": "owner"},
            ).json()

            resp = client.get(
                f"/api/sessions/{created['session_id']}/messages",
                headers={"openemr_user_id": "intruder"},
            )

        # API returns 404 for wrong-user (same as not-found) to avoid leaking session existence
        assert resp.status_code == 404


# ==================================================================
# 5. Verification checks catch dangerous operations
# ==================================================================

class TestVerificationCatchesDangerousOps:

    @pytest.mark.asyncio
    async def test_high_risk_drug_with_allergy_conflict_is_error(self):
        """Warfarin + documented warfarin allergy → must be severity=error, not just warning."""
        client = AsyncMock()
        client.fhir_read.side_effect = [
            {"entry": []},  # MedicationRequest lookup
            {"entry": [{"resource": {"substance": "warfarin"}}]},  # Allergy lookup
        ]
        item = _make_manifest_item(
            resource_type="MedicationRequest",
            action=ManifestAction.CREATE,
            proposed_value={"drug": "Warfarin 5mg"},
            description="Start warfarin",
        )

        results = await check_medication_safety(item, client, "patient-1")

        high_risk = [r for r in results if r.check_name == "medication_high_risk"]
        allergy = [r for r in results if r.check_name == "medication_allergy_conflict"]
        assert len(high_risk) == 1
        assert high_risk[0].severity == "error"
        assert high_risk[0].passed is False
        assert len(allergy) == 1
        assert allergy[0].severity == "error"
        assert allergy[0].passed is False

    def test_dose_over_10000mg_flagged(self):
        item = _make_manifest_item(
            resource_type="MedicationRequest",
            proposed_value={"drug": "TestDrug", "dose": "50000mg daily"},
        )
        results = check_dose_sanity(item)
        assert len(results) == 1
        assert results[0].passed is False
        assert "50000" in results[0].message

    def test_dose_exactly_10000mg_passes(self):
        item = _make_manifest_item(
            resource_type="MedicationRequest",
            proposed_value={"drug": "TestDrug", "dose": "10000mg"},
        )
        results = check_dose_sanity(item)
        assert results == []

    @pytest.mark.asyncio
    async def test_grounding_check_fails_with_no_source_reference(self):
        client = AsyncMock()
        item = _make_manifest_item(source_reference="")
        result = await check_grounding(item, client)
        assert result.passed is False
        assert "No source_reference" in result.message

    @pytest.mark.asyncio
    async def test_grounding_check_fails_with_bad_format(self):
        client = AsyncMock()
        item = _make_manifest_item(source_reference="not-a-valid-ref")
        result = await check_grounding(item, client)
        assert result.passed is False
        assert "Invalid source_reference format" in result.message

    @pytest.mark.asyncio
    async def test_grounding_check_fails_when_resource_not_found(self):
        client = AsyncMock()
        client.fhir_read.return_value = {"total": 0, "entry": []}
        item = _make_manifest_item(
            source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
        )
        result = await check_grounding(item, client)
        assert result.passed is False
        assert "not found" in result.message

    @pytest.mark.asyncio
    async def test_grounding_check_fails_on_fhir_error(self):
        client = AsyncMock()
        client.fhir_read.side_effect = ConnectionError("timeout")
        item = _make_manifest_item(
            source_reference=f"Encounter/{ENCOUNTER_FHIR_UUID}",
        )
        result = await check_grounding(item, client)
        assert result.passed is False
        assert "Failed to fetch" in result.message

    @pytest.mark.asyncio
    async def test_verify_manifest_aggregates_failures(self):
        """verify_manifest should report individual check failures, not crash."""
        client = AsyncMock()
        client.fhir_read.return_value = {"total": 0, "entry": []}
        manifest = ChangeManifest(
            patient_id=PATIENT_FHIR_UUID,
            items=[
                ManifestItem(
                    resource_type="MedicationRequest",
                    action=ManifestAction.CREATE,
                    proposed_value={"drug": "Warfarin 50000mg"},
                    source_reference="",
                    description="possibly dangerous",
                    status="approved",
                ),
            ],
        )

        report = await verify_manifest(manifest, client)

        # Should have grounding failure, confidence warning, high-risk error, dose warning
        assert not report.passed
        grounding = [r for r in report.results if r.check_name == "grounding"]
        assert grounding and not grounding[0].passed
        confidence = [r for r in report.results if r.check_name == "confidence"]
        assert confidence and not confidence[0].passed
        high_risk = [r for r in report.results if r.check_name == "medication_high_risk"]
        assert high_risk and not high_risk[0].passed

    @pytest.mark.asyncio
    async def test_medication_create_without_drug_name_fails_required_fields(self):
        client = AsyncMock()
        item = _make_manifest_item(
            resource_type="MedicationRequest",
            action=ManifestAction.CREATE,
            proposed_value={"dose": "500mg"},
        )
        results = await check_medication_safety(item, client, "patient-1")
        assert any(
            r.check_name == "medication_required_fields" and not r.passed
            for r in results
        )

    def test_constraint_invalid_icd10_fails(self):
        item = _make_manifest_item(
            resource_type="Condition",
            proposed_value={"code": "NOTACODE"},
        )
        results = check_constraints(item)
        assert any(not r.passed for r in results)

    def test_hedging_in_description_detected(self):
        item = _make_manifest_item(
            description="The patient might be diabetic, uncertain diagnosis",
        )
        result = check_confidence(item)
        assert result.passed is False
        assert "might be" in result.message or "uncertain" in result.message
