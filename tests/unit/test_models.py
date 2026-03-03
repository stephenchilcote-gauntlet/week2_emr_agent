from __future__ import annotations

from datetime import datetime

import pytest

from src.agent.models import (
    AgentSession,
    AgentMessage,
    ChangeManifest,
    ManifestAction,
    ManifestItem,
    PageContext,
    ToolCall,
    ToolResult,
)


class TestManifestItem:
    def test_defaults(self):
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.CREATE,
            proposed_value={"code": "E11.9"},
            source_reference="Encounter/5",
            description="Add diagnosis",
        )
        assert item.id  # auto-generated uuid
        assert item.confidence == "high"
        assert item.status == "pending"
        assert item.depends_on == []
        assert item.current_value is None

    def test_explicit_fields(self):
        item = ManifestItem(
            id="custom-id",
            resource_type="Observation",
            action=ManifestAction.UPDATE,
            proposed_value={"value": 120},
            current_value={"value": 110},
            source_reference="Patient/1",
            description="Update BP",
            confidence="medium",
            status="approved",
            depends_on=["dep-1"],
        )
        assert item.id == "custom-id"
        assert item.current_value == {"value": 110}
        assert item.confidence == "medium"
        assert item.depends_on == ["dep-1"]


class TestChangeManifest:
    def test_with_items(self, sample_manifest_item):
        manifest = ChangeManifest(
            patient_id="p1",
            items=[sample_manifest_item],
        )
        assert manifest.id  # auto-generated
        assert manifest.patient_id == "p1"
        assert manifest.encounter_id is None
        assert manifest.status == "draft"
        assert len(manifest.items) == 1
        assert isinstance(manifest.created_at, datetime)

    def test_empty_items_default(self):
        manifest = ChangeManifest(patient_id="p2")
        assert manifest.items == []


class TestManifestAction:
    def test_enum_values(self):
        assert ManifestAction.CREATE.value == "create"
        assert ManifestAction.UPDATE.value == "update"
        assert ManifestAction.DELETE.value == "delete"

    def test_from_string(self):
        assert ManifestAction("create") is ManifestAction.CREATE
        assert ManifestAction("update") is ManifestAction.UPDATE
        assert ManifestAction("delete") is ManifestAction.DELETE


class TestPageContext:
    def test_all_optional(self):
        ctx = PageContext()
        assert ctx.patient_id is None
        assert ctx.encounter_id is None
        assert ctx.page_type is None
        assert ctx.active_form is None

    def test_with_values(self, sample_page_context):
        assert sample_page_context.patient_id == "5"
        assert sample_page_context.encounter_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        assert sample_page_context.page_type == "encounter"


class TestAgentSession:
    def test_defaults(self):
        session = AgentSession()
        assert session.id
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


class TestAgentMessage:
    def test_invalid_role_rejected(self):
        with pytest.raises(ValueError, match="role must be one of"):
            AgentMessage(role="system", content="x")

    def test_tool_message_requires_results(self):
        with pytest.raises(ValueError, match="tool messages must include tool_results"):
            AgentMessage(role="tool", content="")


class TestToolCall:
    def test_creation(self):
        tc = ToolCall(name="fhir_read", arguments={"resource_type": "Patient"}, id="tc-1")
        assert tc.name == "fhir_read"
        assert tc.arguments == {"resource_type": "Patient"}
        assert tc.id == "tc-1"

    def test_serialization(self):
        tc = ToolCall(name="fhir_read", arguments={"resource_type": "Patient"}, id="tc-1")
        data = tc.model_dump()
        assert data == {
            "name": "fhir_read",
            "arguments": {"resource_type": "Patient"},
            "id": "tc-1",
        }
        roundtrip = ToolCall.model_validate(data)
        assert roundtrip == tc


class TestToolResult:
    def test_defaults(self):
        tr = ToolResult(tool_call_id="tc-1", content="result data")
        assert tr.is_error is False

    def test_error_result(self):
        tr = ToolResult(tool_call_id="tc-1", content="something broke", is_error=True)
        assert tr.is_error is True

    def test_serialization(self):
        tr = ToolResult(tool_call_id="tc-1", content='{"key": "val"}', is_error=False)
        data = tr.model_dump()
        assert data == {
            "tool_call_id": "tc-1",
            "content": '{"key": "val"}',
            "is_error": False,
        }
        roundtrip = ToolResult.model_validate(data)
        assert roundtrip == tr


# ---------------------------------------------------------------------------
# Additional edge-case coverage
# ---------------------------------------------------------------------------


class TestManifestItemAdditionalFields:
    def test_execution_result_stored(self):
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.CREATE,
            proposed_value={},
            source_reference="enc/1",
            description="Test",
            execution_result="201 Created",
        )
        assert item.execution_result == "201 Created"

    def test_target_resource_id_stored(self):
        item = ManifestItem(
            resource_type="AllergyIntolerance",
            action=ManifestAction.UPDATE,
            proposed_value={},
            source_reference="enc/1",
            description="Update allergy",
            target_resource_id="allergy-uuid-123",
        )
        assert item.target_resource_id == "allergy-uuid-123"

    def test_status_approved(self):
        item = ManifestItem(
            resource_type="Condition",
            action=ManifestAction.CREATE,
            proposed_value={},
            source_reference="enc/1",
            description="Test",
            status="approved",
        )
        assert item.status == "approved"

    def test_delete_action(self):
        item = ManifestItem(
            resource_type="MedicationRequest",
            action=ManifestAction.DELETE,
            proposed_value={},
            source_reference="enc/1",
            description="Remove medication",
        )
        assert item.action == ManifestAction.DELETE


class TestPageContextAdditionalFields:
    def test_visible_data_stored(self):
        ctx = PageContext(visible_data={"conditions": [{"code": "E11.9"}]})
        assert ctx.visible_data == {"conditions": [{"code": "E11.9"}]}

    def test_active_form_stored(self):
        ctx = PageContext(active_form={"form_type": "vitals", "enc_id": "5"})
        assert ctx.active_form == {"form_type": "vitals", "enc_id": "5"}

    def test_serialization_round_trip(self):
        ctx = PageContext(
            patient_id="p1",
            encounter_id="e1",
            page_type="encounter",
            visible_data={"allergies": []},
        )
        data = ctx.model_dump()
        restored = PageContext.model_validate(data)
        assert restored.patient_id == "p1"
        assert restored.visible_data == {"allergies": []}


class TestAgentSessionAdditionalFields:
    def test_openemr_user_id(self):
        session = AgentSession(openemr_user_id="user-admin")
        assert session.openemr_user_id == "user-admin"

    def test_fhir_patient_id(self):
        session = AgentSession(fhir_patient_id="bbb13f7a-966e-4c7c-aea5-4bac3ce98505")
        assert session.fhir_patient_id == "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"

    def test_openemr_pid(self):
        session = AgentSession(openemr_pid="4")
        assert session.openemr_pid == "4"

    def test_navigate_to_patient(self):
        nav = {"pid": "4", "name": "Maria Santos"}
        session = AgentSession(navigate_to_patient=nav)
        assert session.navigate_to_patient == nav

    def test_serialization_preserves_fields(self):
        session = AgentSession(
            openemr_user_id="admin",
            fhir_patient_id="uuid-1",
            openemr_pid="4",
        )
        data = session.model_dump(mode="json")
        assert data["openemr_user_id"] == "admin"
        assert data["fhir_patient_id"] == "uuid-1"
        assert data["openemr_pid"] == "4"
        assert "created_at" in data


class TestAgentMessageAdditionalFields:
    def test_assistant_with_tool_calls(self):
        tc = ToolCall(name="fhir_read", arguments={"resource_type": "Patient"}, id="tc-1")
        msg = AgentMessage(role="assistant", content="", tool_calls=[tc])
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "fhir_read"

    def test_tool_message_with_results(self):
        tr = ToolResult(tool_call_id="tc-1", content='{"status": "ok"}')
        msg = AgentMessage(role="tool", content="", tool_results=[tr])
        assert len(msg.tool_results) == 1
        assert not msg.tool_results[0].is_error

    def test_assistant_role_accepted(self):
        msg = AgentMessage(role="assistant", content="Here is the result.")
        assert msg.role == "assistant"

    def test_user_role_accepted(self):
        msg = AgentMessage(role="user", content="What are the conditions?")
        assert msg.role == "user"
