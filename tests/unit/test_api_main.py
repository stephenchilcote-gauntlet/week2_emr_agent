from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import anthropic
from fastapi.testclient import TestClient

from src.agent.labels import uuid_to_words
from src.agent.models import AgentMessage, AgentSession, ChangeManifest, ManifestAction, ManifestItem, ToolCall
from src.api.main import _ephemeral_sessions, app

PATIENT_FHIR_UUID = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
PATIENT_PID = "5"


class _DummyAgentLoop:
    async def run(self, session: AgentSession, user_message: str) -> AgentSession:
        session.messages.append(AgentMessage(role="assistant", content=f"echo: {user_message}"))
        return session


class _ToolCallingAgentLoop:
    async def run(self, session: AgentSession, user_message: str) -> AgentSession:
        session.messages.append(AgentMessage(role="user", content=user_message))
        session.messages.append(
            AgentMessage(
                role="assistant",
                content="working",
                tool_calls=[
                    ToolCall(id="1", name="fhir_read", arguments={}),
                    ToolCall(id="2", name="fhir_read", arguments={}),
                    ToolCall(id="3", name="get_page_context", arguments={}),
                ],
            )
        )
        session.messages.append(AgentMessage(role="assistant", content="done"))
        return session

    async def execute_approved(self, session: AgentSession) -> AgentSession:
        session.phase = "complete"
        if session.manifest:
            for item in session.manifest.items:
                if item.status == "approved":
                    item.status = "completed"
                    item.execution_result = "ok"
        return session


def _headers(user_id: str) -> dict[str, str]:
    return {"openemr_user_id": user_id}


def test_auth_header_required() -> None:
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        response = client.post("/api/chat", json={"message": "hello"})
    assert response.status_code == 401


def test_chat_unknown_session_returns_404() -> None:
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        response = client.post(
            "/api/chat",
            headers=_headers("u-1"),
            json={"session_id": "missing", "message": "hello"},
        )
    assert response.status_code == 404


def test_sessions_are_user_scoped() -> None:
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        create = client.post("/api/sessions", headers=_headers("u-1")).json()

        # Send a message so the session gets persisted (empty sessions are ephemeral)
        client.post(
            "/api/chat",
            headers=_headers("u-1"),
            json={"session_id": create["session_id"], "message": "hello"},
        )

        forbidden = client.get(
            f"/api/sessions/{create['session_id']}/messages",
            headers=_headers("u-2"),
        )

        own = client.get("/api/sessions", headers=_headers("u-1")).json()
    assert forbidden.status_code == 403
    assert len(own) >= 1
    assert all(session["session_id"] for session in own)


def test_approve_applies_modified_items() -> None:
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        patient_uuid = "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"
        client.app.state.openemr_client = SimpleNamespace(
            get_fhir_metadata=AsyncMock(return_value={}),
            fhir_read=AsyncMock(return_value={
                "resourceType": "Bundle",
                "total": 1,
                "entry": [{"resource": {"resourceType": "Patient", "id": patient_uuid}}],
            }),
        )

        created = client.post("/api/sessions", headers=_headers("u-1")).json()
        session = _ephemeral_sessions[created["session_id"]]
        session.manifest = ChangeManifest(
            patient_id=PATIENT_FHIR_UUID,
            items=[
                ManifestItem(
                    id="item-1",
                    resource_type="Condition",
                    action=ManifestAction.CREATE,
                    proposed_value={"code": "E11.9"},
                    source_reference="Encounter/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    description="initial",
                )
            ],
        )
        client.app.state.session_store.save(session)

        response = client.post(
            f"/api/manifest/{session.id}/approve",
            headers=_headers("u-1"),
            json={
                "approved_items": ["item-1"],
                "modified_items": [
                    {"id": "item-1", "proposed_value": {"code": "I10"}}
                ],
            },
        )

        updated = client.app.state.session_store.load(session.id)
    assert response.status_code == 200
    assert updated is not None
    assert updated.manifest is not None
    assert updated.manifest.items[0].proposed_value["code"] == "I10"


def test_chat_returns_grouped_tool_summary() -> None:
    with TestClient(app) as client:
        client.app.state.agent_loop = _ToolCallingAgentLoop()
        response = client.post(
            "/api/chat",
            headers=_headers("u-tools"),
            json={"message": "summarize tools"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["response"] == "done"
    assert payload["tool_calls_summary"] == [
        {"name": "fhir_read", "count": 2},
        {"name": "get_page_context", "count": 1},
    ]


def test_sessions_include_patient_context_metadata() -> None:
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        _ = client.post(
            "/api/chat",
            headers=_headers("u-meta"),
            json={
                "message": "hello",
                "page_context": {
                    "patient_id": "123",
                    "visible_data": {"patient_name": "Maria Santos"},
                },
            },
        )
        listed = client.get("/api/sessions", headers=_headers("u-meta"))

    assert listed.status_code == 200
    item = listed.json()[0]
    assert item["patient_id"] == "123"
    assert item["patient_name"] == "Maria Santos"


def test_sidebar_ui_routes_are_served() -> None:
    with TestClient(app) as client:
        index = client.get("/ui")
        js = client.get("/ui/assets/sidebar.js")

    assert index.status_code == 200
    assert "Clinical Assistant" in index.text
    assert js.status_code == 200
    assert "class SidebarApp" in js.text


# ------------------------------------------------------------------
# Patient resolution: pid → FHIR UUID → label registry
# These tests guard against the bugs where:
#   - main.py used {"_id": pid} instead of {"identifier": pid}
#   - Resolved UUID was not registered in label_registry
#   - session.fhir_patient_id was never set
# ------------------------------------------------------------------


def test_chat_resolves_pid_to_fhir_uuid_using_identifier_param() -> None:
    """The chat endpoint must use FHIR 'identifier' (not '_id') to resolve
    an internal OpenEMR pid to a FHIR UUID.  _id expects a UUID and would
    fail silently when given a pid like '5'."""
    fhir_read_mock = AsyncMock(return_value={
        "entry": [{"resource": {"resourceType": "Patient", "id": PATIENT_FHIR_UUID}}],
    })
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        client.app.state.openemr_client = SimpleNamespace(
            get_fhir_metadata=AsyncMock(return_value={}),
            fhir_read=fhir_read_mock,
        )

        resp = client.post(
            "/api/chat",
            headers=_headers("u-resolve"),
            json={
                "message": "hello",
                "page_context": {"patient_id": PATIENT_PID},
            },
        )

    assert resp.status_code == 200
    fhir_read_mock.assert_awaited_once_with("Patient", {"identifier": PATIENT_PID})


def test_chat_sets_fhir_patient_id() -> None:
    """After resolving a pid, the session must have fhir_patient_id set."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        client.app.state.openemr_client = SimpleNamespace(
            get_fhir_metadata=AsyncMock(return_value={}),
            fhir_read=AsyncMock(return_value={
                "entry": [{"resource": {"resourceType": "Patient", "id": PATIENT_FHIR_UUID}}],
            }),
        )

        resp = client.post(
            "/api/chat",
            headers=_headers("u-label"),
            json={
                "message": "hello",
                "page_context": {"patient_id": PATIENT_PID},
            },
        )
        assert resp.status_code == 200

        session_id = resp.json()["session_id"]
        session = client.app.state.session_store.load(session_id)

    assert session is not None
    assert session.fhir_patient_id == PATIENT_FHIR_UUID


def test_chat_skips_patient_lookup_when_fhir_id_already_set() -> None:
    """Once fhir_patient_id is set, subsequent chat calls should NOT
    re-resolve the patient — avoids redundant FHIR calls."""
    fhir_read_mock = AsyncMock(return_value={
        "entry": [{"resource": {"resourceType": "Patient", "id": PATIENT_FHIR_UUID}}],
    })
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        client.app.state.openemr_client = SimpleNamespace(
            get_fhir_metadata=AsyncMock(return_value={}),
            fhir_read=fhir_read_mock,
        )

        # First call — should resolve
        resp1 = client.post(
            "/api/chat",
            headers=_headers("u-skip"),
            json={
                "message": "hello",
                "page_context": {"patient_id": PATIENT_PID},
            },
        )
        session_id = resp1.json()["session_id"]

        # Second call — should NOT resolve again
        fhir_read_mock.reset_mock()
        resp2 = client.post(
            "/api/chat",
            headers=_headers("u-skip"),
            json={
                "session_id": session_id,
                "message": "follow up",
                "page_context": {"patient_id": PATIENT_PID},
            },
        )

    assert resp2.status_code == 200
    fhir_read_mock.assert_not_awaited()


def test_health_check_returns_ok_when_connected() -> None:
    with TestClient(app) as client:
        client.app.state.openemr_client = SimpleNamespace(
            get_fhir_metadata=AsyncMock(return_value={"resourceType": "CapabilityStatement"}),
        )
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["openemr_connected"] is True
    assert body["openemr_status"] == "ok"


def test_health_check_returns_error_when_metadata_has_error() -> None:
    with TestClient(app) as client:
        client.app.state.openemr_client = SimpleNamespace(
            get_fhir_metadata=AsyncMock(return_value={"error": "unauthorized"}),
        )
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["openemr_connected"] is False
    assert body["openemr_status"] == "error"


def test_health_check_returns_error_when_metadata_raises() -> None:
    with TestClient(app) as client:
        client.app.state.openemr_client = SimpleNamespace(
            get_fhir_metadata=AsyncMock(side_effect=RuntimeError("connection refused")),
        )
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["openemr_connected"] is False
    assert body["openemr_status"] == "error"


def test_get_manifest_returns_manifest_when_present() -> None:
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        created = client.post("/api/sessions", headers=_headers("u-mf")).json()
        session = _ephemeral_sessions[created["session_id"]]
        session.manifest = ChangeManifest(
            patient_id=PATIENT_FHIR_UUID,
            items=[
                ManifestItem(
                    id="item-1",
                    resource_type="Condition",
                    action=ManifestAction.CREATE,
                    proposed_value={"code": "E11.9"},
                    source_reference="Encounter/abc",
                    description="Test",
                )
            ],
        )
        client.app.state.session_store.save(session)

        response = client.get(
            f"/api/manifest/{session.id}",
            headers=_headers("u-mf"),
        )
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session.id
    assert body["manifest"] is not None
    assert len(body["manifest"]["items"]) == 1


def test_get_manifest_returns_null_when_no_manifest() -> None:
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        created = client.post("/api/sessions", headers=_headers("u-mf2")).json()

        response = client.get(
            f"/api/manifest/{created['session_id']}",
            headers=_headers("u-mf2"),
        )
    assert response.status_code == 200
    assert response.json()["manifest"] is None


def test_get_manifest_forbidden_for_wrong_user() -> None:
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        created = client.post("/api/sessions", headers=_headers("u-own")).json()

        response = client.get(
            f"/api/manifest/{created['session_id']}",
            headers=_headers("u-other"),
        )
    assert response.status_code == 403


def test_get_session_audit_returns_events() -> None:
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        created = client.post("/api/sessions", headers=_headers("u-aud")).json()
        sid = created["session_id"]

        # Chat to generate audit events
        client.post(
            "/api/chat",
            headers=_headers("u-aud"),
            json={"session_id": sid, "message": "hi"},
        )

        response = client.get(
            f"/api/sessions/{sid}/audit",
            headers=_headers("u-aud"),
        )
    assert response.status_code == 200
    events = response.json()
    assert isinstance(events, list)
    assert len(events) >= 1
    assert all("event_type" in e for e in events)


def test_get_session_audit_empty_for_new_session() -> None:
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        created = client.post("/api/sessions", headers=_headers("u-aud2")).json()

        response = client.get(
            f"/api/sessions/{created['session_id']}/audit",
            headers=_headers("u-aud2"),
        )
    assert response.status_code == 200
    assert response.json() == []


def test_chat_returns_502_on_llm_api_error() -> None:
    import httpx

    class _ErrorAgentLoop:
        async def run(self, session, user_message):
            raise anthropic.APIStatusError(
                message="overloaded",
                response=httpx.Response(529, request=httpx.Request("POST", "https://api.anthropic.com")),
                body=None,
            )

    with TestClient(app, raise_server_exceptions=False) as client:
        client.app.state.agent_loop = _ErrorAgentLoop()
        response = client.post(
            "/api/chat",
            headers=_headers("u-err"),
            json={"message": "hello"},
        )
    assert response.status_code == 502
    assert "LLM API error" in response.json()["detail"]


def test_chat_handles_empty_patient_search_gracefully() -> None:
    """If FHIR search returns no entries for a pid, fhir_patient_id
    should remain None — no crash, no garbage data."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        client.app.state.openemr_client = SimpleNamespace(
            get_fhir_metadata=AsyncMock(return_value={}),
            fhir_read=AsyncMock(return_value={"entry": []}),
        )

        resp = client.post(
            "/api/chat",
            headers=_headers("u-empty"),
            json={
                "message": "hello",
                "page_context": {"patient_id": "999"},
            },
        )
        assert resp.status_code == 200

        session_id = resp.json()["session_id"]
        session = client.app.state.session_store.load(session_id)

    assert session is not None
    assert session.fhir_patient_id is None
