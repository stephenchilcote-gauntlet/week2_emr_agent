from __future__ import annotations

from datetime import datetime

import pytest

from src.agent.models import (
    AgentSession,
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
        assert sample_page_context.patient_id == "patient-1"
        assert sample_page_context.encounter_id == "encounter-5"
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
