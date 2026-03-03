"""Unit tests for API request/response schemas (src/api/schemas.py)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.schemas import (
    ApprovalRequest,
    ApprovalResponse,
    ChatRequest,
    ChatResponse,
    FeedbackRequest,
    HealthResponse,
    ManifestResponse,
    PageContextRequest,
)


# ---------------------------------------------------------------------------
# PageContextRequest
# ---------------------------------------------------------------------------


class TestPageContextRequest:
    def test_string_patient_id_passed_through(self) -> None:
        ctx = PageContextRequest(patient_id="42")
        assert ctx.patient_id == "42"

    def test_integer_patient_id_coerced_to_string(self) -> None:
        """Older embed.js versions send integer PIDs — must be accepted."""
        ctx = PageContextRequest(patient_id=42)  # type: ignore[arg-type]
        assert ctx.patient_id == "42"

    def test_none_patient_id_remains_none(self) -> None:
        ctx = PageContextRequest(patient_id=None)
        assert ctx.patient_id is None

    def test_integer_encounter_id_coerced_to_string(self) -> None:
        ctx = PageContextRequest(encounter_id=999)  # type: ignore[arg-type]
        assert ctx.encounter_id == "999"

    def test_none_encounter_id_remains_none(self) -> None:
        ctx = PageContextRequest(encounter_id=None)
        assert ctx.encounter_id is None

    def test_page_type_optional(self) -> None:
        ctx = PageContextRequest(page_type="enc")
        assert ctx.page_type == "enc"

    def test_all_fields_none_by_default(self) -> None:
        ctx = PageContextRequest()
        assert ctx.patient_id is None
        assert ctx.encounter_id is None
        assert ctx.page_type is None
        assert ctx.visible_data is None

    def test_visible_data_dict_accepted(self) -> None:
        ctx = PageContextRequest(visible_data={"conditions": ["E11.9"]})
        assert ctx.visible_data == {"conditions": ["E11.9"]}

    def test_zero_patient_id_coerced_to_string(self) -> None:
        """Zero integer PID is coerced to '0' (not treated as falsy None)."""
        ctx = PageContextRequest(patient_id=0)  # type: ignore[arg-type]
        assert ctx.patient_id == "0"


# ---------------------------------------------------------------------------
# ChatRequest
# ---------------------------------------------------------------------------


class TestChatRequest:
    def test_message_required(self) -> None:
        with pytest.raises(ValidationError):
            ChatRequest()  # type: ignore[call-arg]

    def test_session_id_optional(self) -> None:
        req = ChatRequest(message="hello")
        assert req.session_id is None

    def test_session_id_provided(self) -> None:
        req = ChatRequest(session_id="abc-123", message="hello")
        assert req.session_id == "abc-123"

    def test_page_context_optional(self) -> None:
        req = ChatRequest(message="hello")
        assert req.page_context is None

    def test_page_context_provided(self) -> None:
        req = ChatRequest(
            message="hello",
            page_context=PageContextRequest(patient_id="42"),
        )
        assert req.page_context is not None
        assert req.page_context.patient_id == "42"


# ---------------------------------------------------------------------------
# ApprovalRequest
# ---------------------------------------------------------------------------


class TestApprovalRequest:
    def test_all_lists_default_empty(self) -> None:
        req = ApprovalRequest()
        assert req.approved_items == []
        assert req.rejected_items == []
        assert req.modified_items == []

    def test_approved_items_provided(self) -> None:
        req = ApprovalRequest(approved_items=["item-1", "item-2"])
        assert req.approved_items == ["item-1", "item-2"]

    def test_modified_items_provided(self) -> None:
        req = ApprovalRequest(
            modified_items=[{"id": "item-1", "proposed_value": {"code": "I10"}}]
        )
        assert len(req.modified_items) == 1
        assert req.modified_items[0]["id"] == "item-1"

    def test_mixed_approval_rejection(self) -> None:
        req = ApprovalRequest(
            approved_items=["a"],
            rejected_items=["b"],
        )
        assert "a" in req.approved_items
        assert "b" in req.rejected_items


# ---------------------------------------------------------------------------
# ChatResponse
# ---------------------------------------------------------------------------


class TestChatResponse:
    def test_minimal_chat_response(self) -> None:
        resp = ChatResponse(
            session_id="sess-1",
            response="Hello",
            phase="ready",
        )
        assert resp.session_id == "sess-1"
        assert resp.response == "Hello"
        assert resp.phase == "ready"
        assert resp.manifest is None
        assert resp.error is None
        assert resp.tool_calls_summary is None

    def test_with_manifest(self) -> None:
        resp = ChatResponse(
            session_id="sess-1",
            response="See manifest",
            phase="reviewing",
            manifest={"id": "m-1", "items": []},
        )
        assert resp.manifest is not None
        assert resp.manifest["id"] == "m-1"

    def test_with_error(self) -> None:
        resp = ChatResponse(
            session_id="sess-1",
            response="",
            phase="ready",
            error="Something went wrong",
        )
        assert resp.error == "Something went wrong"

    def test_navigate_to_patient_field(self) -> None:
        resp = ChatResponse(
            session_id="sess-1",
            response="Opening patient chart",
            phase="ready",
            navigate_to_patient={"pid": "42", "name": "Maria Santos"},
        )
        assert resp.navigate_to_patient == {"pid": "42", "name": "Maria Santos"}


# ---------------------------------------------------------------------------
# ApprovalResponse
# ---------------------------------------------------------------------------


class TestApprovalResponse:
    def test_required_fields(self) -> None:
        resp = ApprovalResponse(
            session_id="sess-1",
            manifest_id="m-1",
            passed=True,
        )
        assert resp.session_id == "sess-1"
        assert resp.manifest_id == "m-1"
        assert resp.passed is True
        assert resp.results == []

    def test_with_results(self) -> None:
        resp = ApprovalResponse(
            session_id="sess-1",
            manifest_id="m-1",
            passed=False,
            results=[{"item_id": "i-1", "status": "failed"}],
        )
        assert len(resp.results) == 1
        assert resp.results[0]["status"] == "failed"


# ---------------------------------------------------------------------------
# ManifestResponse
# ---------------------------------------------------------------------------


class TestManifestResponse:
    def test_manifest_optional(self) -> None:
        resp = ManifestResponse(session_id="sess-1")
        assert resp.manifest is None

    def test_with_manifest_dict(self) -> None:
        resp = ManifestResponse(
            session_id="sess-1",
            manifest={"id": "m-1"},
        )
        assert resp.manifest == {"id": "m-1"}


# ---------------------------------------------------------------------------
# FeedbackRequest
# ---------------------------------------------------------------------------


class TestFeedbackRequest:
    def test_up_rating(self) -> None:
        req = FeedbackRequest(message_index=0, rating="up")
        assert req.rating == "up"

    def test_down_rating(self) -> None:
        req = FeedbackRequest(message_index=2, rating="down")
        assert req.rating == "down"
        assert req.message_index == 2

    def test_message_index_required(self) -> None:
        with pytest.raises(ValidationError):
            FeedbackRequest(rating="up")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# HealthResponse
# ---------------------------------------------------------------------------


class TestHealthResponse:
    def test_healthy(self) -> None:
        resp = HealthResponse(
            status="ok",
            openemr_connected=True,
            openemr_status="Connected",
        )
        assert resp.status == "ok"
        assert resp.openemr_connected is True

    def test_unhealthy(self) -> None:
        resp = HealthResponse(
            status="error",
            openemr_connected=False,
            openemr_status="Connection refused",
        )
        assert resp.openemr_connected is False
        assert "refused" in resp.openemr_status
