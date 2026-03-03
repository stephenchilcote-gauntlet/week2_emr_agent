from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, call
from uuid import uuid4

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

        # No patient_id → returns sessions with no patient context
        own = client.get("/api/sessions", headers=_headers("u-1")).json()
    # DB-level scoping: wrong user sees 404, not 403 (no info leakage)
    assert forbidden.status_code == 404
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

        updated = client.app.state.session_store.load(session.id, "u-1")
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


def test_sessions_scoped_by_patient_id() -> None:
    """Sessions for patient A must NOT appear when listing for patient B.
    This is the core medical-data isolation guarantee."""
    uid = f"u-scope-{uuid4().hex[:8]}"
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()

        # Create a session for patient 5
        client.post(
            "/api/chat",
            headers=_headers(uid),
            json={"message": "patient 5 note", "page_context": {"patient_id": "5"}},
        )

        # Create a session for patient 7
        client.post(
            "/api/chat",
            headers=_headers(uid),
            json={"message": "patient 7 note", "page_context": {"patient_id": "7"}},
        )

        # Create a session with no patient
        client.post(
            "/api/chat",
            headers=_headers(uid),
            json={"message": "general question"},
        )

        p5 = client.get("/api/sessions?patient_id=5", headers=_headers(uid)).json()
        p7 = client.get("/api/sessions?patient_id=7", headers=_headers(uid)).json()
        no_patient = client.get("/api/sessions", headers=_headers(uid)).json()

    assert len(p5) == 1
    assert p5[0]["patient_id"] == "5"

    assert len(p7) == 1
    assert p7[0]["patient_id"] == "7"

    assert len(no_patient) == 1
    assert no_patient[0]["patient_id"] is None


def test_sessions_patient_isolation_across_users() -> None:
    """Even with matching patient_id, sessions are still user-scoped."""
    alice = f"doc-alice-{uuid4().hex[:8]}"
    bob = f"doc-bob-{uuid4().hex[:8]}"
    pid = f"pat-{uuid4().hex[:8]}"
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()

        client.post(
            "/api/chat",
            headers=_headers(alice),
            json={"message": "alice note", "page_context": {"patient_id": pid}},
        )

        client.post(
            "/api/chat",
            headers=_headers(bob),
            json={"message": "bob note", "page_context": {"patient_id": pid}},
        )

        alice_sessions = client.get(
            f"/api/sessions?patient_id={pid}", headers=_headers(alice),
        ).json()
        bob_sessions = client.get(
            f"/api/sessions?patient_id={pid}", headers=_headers(bob),
        ).json()

    assert len(alice_sessions) == 1
    assert len(bob_sessions) == 1
    assert alice_sessions[0]["session_id"] != bob_sessions[0]["session_id"]


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
    """The chat endpoint resolves a pid to a FHIR UUID by scanning the REST
    patient list (api_call('patient')), not via FHIR identifier search.
    This is required because seed patients only have SSN identifiers, not PT."""
    api_call_mock = AsyncMock(return_value={
        "data": [{"pid": PATIENT_PID, "uuid": PATIENT_FHIR_UUID}],
    })
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        client.app.state.openemr_client = SimpleNamespace(
            get_fhir_metadata=AsyncMock(return_value={}),
            api_call=api_call_mock,
            fhir_read=AsyncMock(return_value={}),
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
    api_call_mock.assert_awaited_once_with("patient")


def test_chat_sets_fhir_patient_id() -> None:
    """After resolving a pid via REST scan, the session must have fhir_patient_id set."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        client.app.state.openemr_client = SimpleNamespace(
            get_fhir_metadata=AsyncMock(return_value={}),
            api_call=AsyncMock(return_value={
                "data": [{"pid": PATIENT_PID, "uuid": PATIENT_FHIR_UUID}],
            }),
            fhir_read=AsyncMock(return_value={}),
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
        session = client.app.state.session_store.load(session_id, "u-label")

    assert session is not None
    assert session.fhir_patient_id == PATIENT_FHIR_UUID


def test_chat_skips_patient_lookup_when_fhir_id_already_set() -> None:
    """Once fhir_patient_id is set, subsequent chat calls should NOT
    re-resolve the patient — avoids redundant REST API calls."""
    api_call_mock = AsyncMock(return_value={
        "data": [{"pid": PATIENT_PID, "uuid": PATIENT_FHIR_UUID}],
    })
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        client.app.state.openemr_client = SimpleNamespace(
            get_fhir_metadata=AsyncMock(return_value={}),
            api_call=api_call_mock,
            fhir_read=AsyncMock(return_value={}),
        )

        # First call — should resolve via api_call
        resp1 = client.post(
            "/api/chat",
            headers=_headers("u-skip"),
            json={
                "message": "hello",
                "page_context": {"patient_id": PATIENT_PID},
            },
        )
        session_id = resp1.json()["session_id"]

        # Second call — should NOT call api_call again
        api_call_mock.reset_mock()
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
    api_call_mock.assert_not_awaited()


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
    # DB-level scoping: wrong user sees 404 (session doesn't exist for them)
    assert response.status_code == 404


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
    """If REST patient scan and FHIR name fallback both return nothing for a pid,
    fhir_patient_id should remain None — no crash, no garbage data."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        client.app.state.openemr_client = SimpleNamespace(
            get_fhir_metadata=AsyncMock(return_value={}),
            # REST scan returns empty list (no patient with pid 999)
            api_call=AsyncMock(return_value={"data": []}),
            # FHIR name fallback also returns nothing (no visible_data.patient_name)
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
        session = client.app.state.session_store.load(session_id, "u-empty")

    assert session is not None
    assert session.fhir_patient_id is None


# ------------------------------------------------------------------
# DELETE /api/sessions/{session_id}
# ------------------------------------------------------------------


def test_delete_session_returns_204() -> None:
    """Deleting a session returns 204 No Content."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        # Chat first to persist the session
        resp = client.post(
            "/api/chat",
            headers=_headers("u-del"),
            json={"message": "hello"},
        )
        sid = resp.json()["session_id"]

        delete_resp = client.delete(
            f"/api/sessions/{sid}",
            headers=_headers("u-del"),
        )

    assert delete_resp.status_code == 204


def test_delete_session_makes_it_unfetchable() -> None:
    """After deletion, the session is no longer accessible."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        resp = client.post(
            "/api/chat",
            headers=_headers("u-del2"),
            json={"message": "hello"},
        )
        sid = resp.json()["session_id"]

        client.delete(f"/api/sessions/{sid}", headers=_headers("u-del2"))

        # After deletion, GET messages should return 404
        messages_resp = client.get(
            f"/api/sessions/{sid}/messages",
            headers=_headers("u-del2"),
        )

    assert messages_resp.status_code == 404


def test_delete_session_wrong_user_returns_404() -> None:
    """Deleting another user's session returns 404 (no info leakage)."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        resp = client.post(
            "/api/chat",
            headers=_headers("u-owner"),
            json={"message": "hello"},
        )
        sid = resp.json()["session_id"]

        delete_resp = client.delete(
            f"/api/sessions/{sid}",
            headers=_headers("u-attacker"),
        )

    assert delete_resp.status_code == 404


def test_delete_nonexistent_session_returns_404() -> None:
    """Deleting a session that doesn't exist returns 404."""
    with TestClient(app) as client:
        resp = client.delete(
            "/api/sessions/does-not-exist",
            headers=_headers("u-any"),
        )
    assert resp.status_code == 404


# ------------------------------------------------------------------
# GET /api/sessions/{session_id}/messages
# ------------------------------------------------------------------


def test_get_session_messages_returns_messages() -> None:
    """GET /messages returns all messages in the session."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        resp = client.post(
            "/api/chat",
            headers=_headers("u-msgs"),
            json={"message": "hello"},
        )
        sid = resp.json()["session_id"]

        msgs_resp = client.get(
            f"/api/sessions/{sid}/messages",
            headers=_headers("u-msgs"),
        )

    assert msgs_resp.status_code == 200
    body = msgs_resp.json()
    assert body["session_id"] == sid
    assert isinstance(body["messages"], list)
    assert len(body["messages"]) >= 1
    # DummyAgentLoop appends: "echo: hello" as assistant
    assert any(m["role"] == "assistant" for m in body["messages"])
    assert "phase" in body


def test_get_session_messages_wrong_user_returns_404() -> None:
    """GET /messages for another user's session returns 404."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        resp = client.post(
            "/api/chat",
            headers=_headers("u-msg-own"),
            json={"message": "hello"},
        )
        sid = resp.json()["session_id"]

        msgs_resp = client.get(
            f"/api/sessions/{sid}/messages",
            headers=_headers("u-msg-other"),
        )

    assert msgs_resp.status_code == 404


def test_get_session_messages_includes_manifest_when_present() -> None:
    """GET /messages includes the current manifest in the response body."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        created = client.post("/api/sessions", headers=_headers("u-msg-mf")).json()
        session = _ephemeral_sessions[created["session_id"]]
        session.manifest = ChangeManifest(
            patient_id=PATIENT_FHIR_UUID,
            items=[
                ManifestItem(
                    id="m-1",
                    resource_type="AllergyIntolerance",
                    action=ManifestAction.CREATE,
                    proposed_value={"substance": "Penicillin"},
                    source_reference="Encounter/aaa",
                    description="Allergy",
                )
            ],
        )
        # Persist so it's loadable
        client.app.state.session_store.save(session)

        msgs_resp = client.get(
            f"/api/sessions/{session.id}/messages",
            headers=_headers("u-msg-mf"),
        )

    assert msgs_resp.status_code == 200
    body = msgs_resp.json()
    assert body["manifest"] is not None
    assert len(body["manifest"]["items"]) == 1


# ------------------------------------------------------------------
# POST /api/manifest/{session_id}/execute
# ------------------------------------------------------------------


def test_execute_manifest_marks_approved_items_completed() -> None:
    """Executing a manifest with approved items marks them 'completed'."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _ToolCallingAgentLoop()
        created = client.post("/api/sessions", headers=_headers("u-exec")).json()
        session = _ephemeral_sessions[created["session_id"]]
        session.manifest = ChangeManifest(
            patient_id=PATIENT_FHIR_UUID,
            items=[
                ManifestItem(
                    id="e-1",
                    resource_type="Condition",
                    action=ManifestAction.CREATE,
                    proposed_value={"code": "E11.9"},
                    source_reference="Encounter/abc",
                    description="Diabetes",
                    status="approved",
                )
            ],
        )
        client.app.state.session_store.save(session)

        resp = client.post(
            f"/api/manifest/{session.id}/execute",
            headers=_headers("u-exec"),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == session.id
    assert body["manifest_status"] == "completed"
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == "e-1"
    assert body["items"][0]["status"] == "completed"
    assert body["items"][0]["resource_type"] == "Condition"


def test_execute_manifest_without_manifest_returns_400() -> None:
    """Attempting to execute when there is no manifest returns 400."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        created = client.post("/api/sessions", headers=_headers("u-exec-no")).json()
        # Persist without manifest
        session = _ephemeral_sessions[created["session_id"]]
        client.app.state.session_store.save(session)

        resp = client.post(
            f"/api/manifest/{session.id}/execute",
            headers=_headers("u-exec-no"),
        )

    assert resp.status_code == 400
    assert "No manifest" in resp.json()["detail"]


def test_execute_manifest_returns_409_when_already_executing() -> None:
    """Attempting to execute an already-executing manifest returns 409."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        created = client.post("/api/sessions", headers=_headers("u-exec-dup")).json()
        session = _ephemeral_sessions[created["session_id"]]
        session.manifest = ChangeManifest(
            patient_id=PATIENT_FHIR_UUID,
            items=[
                ManifestItem(
                    id="dup-1",
                    resource_type="Condition",
                    action=ManifestAction.CREATE,
                    proposed_value={"code": "I10"},
                    source_reference="Encounter/abc",
                    description="Hypertension",
                    status="approved",
                )
            ],
        )
        # Mark manifest as already completed
        session.manifest.status = "completed"
        client.app.state.session_store.save(session)

        resp = client.post(
            f"/api/manifest/{session.id}/execute",
            headers=_headers("u-exec-dup"),
        )

    assert resp.status_code == 409
    assert "completed" in resp.json()["detail"]


def test_execute_manifest_wrong_user_returns_404() -> None:
    """Executing another user's manifest returns 404."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _ToolCallingAgentLoop()
        created = client.post("/api/sessions", headers=_headers("u-exec-own")).json()
        session = _ephemeral_sessions[created["session_id"]]
        session.manifest = ChangeManifest(
            patient_id=PATIENT_FHIR_UUID,
            items=[
                ManifestItem(
                    id="x-1",
                    resource_type="Condition",
                    action=ManifestAction.CREATE,
                    proposed_value={},
                    source_reference="Encounter/abc",
                    description="test",
                    status="approved",
                )
            ],
        )
        client.app.state.session_store.save(session)

        resp = client.post(
            f"/api/manifest/{session.id}/execute",
            headers=_headers("u-exec-bad"),
        )

    assert resp.status_code == 404


# ------------------------------------------------------------------
# POST /api/sessions/{session_id}/feedback
# ------------------------------------------------------------------


def test_feedback_returns_204() -> None:
    """Feedback endpoint returns 204 No Content."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        resp = client.post(
            "/api/chat",
            headers=_headers("u-fb"),
            json={"message": "hello"},
        )
        sid = resp.json()["session_id"]

        fb_resp = client.post(
            f"/api/sessions/{sid}/feedback",
            headers=_headers("u-fb"),
            json={"message_index": 0, "rating": "up"},
        )

    assert fb_resp.status_code == 204


def test_feedback_records_audit_event() -> None:
    """Feedback submission creates an audit event with rating and index."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        resp = client.post(
            "/api/chat",
            headers=_headers("u-fb2"),
            json={"message": "hello"},
        )
        sid = resp.json()["session_id"]

        client.post(
            f"/api/sessions/{sid}/feedback",
            headers=_headers("u-fb2"),
            json={"message_index": 1, "rating": "down"},
        )

        audit_resp = client.get(
            f"/api/sessions/{sid}/audit",
            headers=_headers("u-fb2"),
        )

    events = audit_resp.json()
    fb_events = [e for e in events if e["event_type"] == "message_feedback"]
    assert len(fb_events) == 1
    assert fb_events[0]["details"]["rating"] == "down"
    assert fb_events[0]["details"]["message_index"] == 1


def test_feedback_wrong_user_returns_404() -> None:
    """Feedback for another user's session returns 404."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        resp = client.post(
            "/api/chat",
            headers=_headers("u-fb-own"),
            json={"message": "hello"},
        )
        sid = resp.json()["session_id"]

        fb_resp = client.post(
            f"/api/sessions/{sid}/feedback",
            headers=_headers("u-fb-other"),
            json={"message_index": 0, "rating": "up"},
        )

    assert fb_resp.status_code == 404


# ------------------------------------------------------------------
# Manifest approval edge cases
# ------------------------------------------------------------------


def test_approve_all_rejected_clears_manifest() -> None:
    """When all items are rejected, the manifest is cleared so the agent
    can propose new changes."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        client.app.state.openemr_client = SimpleNamespace(
            get_fhir_metadata=AsyncMock(return_value={}),
        )
        created = client.post("/api/sessions", headers=_headers("u-rej")).json()
        session = _ephemeral_sessions[created["session_id"]]
        session.manifest = ChangeManifest(
            patient_id=PATIENT_FHIR_UUID,
            items=[
                ManifestItem(
                    id="r-1",
                    resource_type="Condition",
                    action=ManifestAction.CREATE,
                    proposed_value={"code": "I10"},
                    source_reference="Encounter/abc",
                    description="Hypertension",
                )
            ],
        )
        client.app.state.session_store.save(session)

        resp = client.post(
            f"/api/manifest/{session.id}/approve",
            headers=_headers("u-rej"),
            json={"rejected_items": ["r-1"]},
        )

        updated = client.app.state.session_store.load(session.id, "u-rej")

    assert resp.status_code == 200
    assert resp.json()["passed"] is True
    # Manifest should be cleared after full rejection
    assert updated is not None
    assert updated.manifest is None
    assert updated.phase == "planning"


def test_approve_manifest_returns_409_when_already_completed() -> None:
    """Approving a manifest that's already 'completed' returns 409."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        created = client.post("/api/sessions", headers=_headers("u-dup-appr")).json()
        session = _ephemeral_sessions[created["session_id"]]
        session.manifest = ChangeManifest(
            patient_id=PATIENT_FHIR_UUID,
            items=[
                ManifestItem(
                    id="dup-a-1",
                    resource_type="Condition",
                    action=ManifestAction.CREATE,
                    proposed_value={"code": "I10"},
                    source_reference="Encounter/abc",
                    description="test",
                )
            ],
        )
        session.manifest.status = "completed"
        client.app.state.session_store.save(session)

        resp = client.post(
            f"/api/manifest/{session.id}/approve",
            headers=_headers("u-dup-appr"),
            json={"approved_items": ["dup-a-1"]},
        )

    assert resp.status_code == 409
    assert "completed" in resp.json()["detail"]


# ------------------------------------------------------------------
# Patient resolution via REST fallback name search
# ------------------------------------------------------------------


def test_chat_falls_back_to_fhir_name_search_when_rest_scan_misses() -> None:
    """If REST scan returns no match for the pid, the code falls back to
    FHIR name search using the patient_name from visible_data."""
    fhir_read_mock = AsyncMock(return_value={
        "entry": [{"resource": {"resourceType": "Patient", "id": PATIENT_FHIR_UUID}}],
    })
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        client.app.state.openemr_client = SimpleNamespace(
            get_fhir_metadata=AsyncMock(return_value={}),
            # REST scan: no patient with pid 999
            api_call=AsyncMock(return_value={"data": []}),
            fhir_read=fhir_read_mock,
        )

        resp = client.post(
            "/api/chat",
            headers=_headers("u-fallback"),
            json={
                "message": "hello",
                "page_context": {
                    "patient_id": "999",
                    "visible_data": {"patient_name": "Maria Santos"},
                },
            },
        )
        assert resp.status_code == 200
        session_id = resp.json()["session_id"]
        session = client.app.state.session_store.load(session_id, "u-fallback")

    # FHIR fallback should have been called with "Santos" (last name token)
    fhir_read_mock.assert_awaited_once_with(
        "Patient", {"name": "Santos", "_count": "5"}
    )
    assert session is not None
    assert session.fhir_patient_id == PATIENT_FHIR_UUID


def test_chat_fhir_patient_id_not_set_when_no_pid_in_context() -> None:
    """If no patient_id is in page_context, no patient resolution runs."""
    api_call_mock = AsyncMock(return_value={"data": []})
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        client.app.state.openemr_client = SimpleNamespace(
            get_fhir_metadata=AsyncMock(return_value={}),
            api_call=api_call_mock,
            fhir_read=AsyncMock(return_value={}),
        )

        resp = client.post(
            "/api/chat",
            headers=_headers("u-nopid"),
            json={"message": "general question"},  # no page_context
        )

    assert resp.status_code == 200
    # api_call should NOT have been called (no patient_id in context)
    api_call_mock.assert_not_awaited()


# ------------------------------------------------------------------
# GET /api/fhir/metadata
# ------------------------------------------------------------------


def test_fhir_metadata_endpoint_proxies_to_openemr() -> None:
    """GET /api/fhir/metadata returns FHIR capability statement from OpenEMR."""
    capability = {"resourceType": "CapabilityStatement", "fhirVersion": "4.0.1"}
    with TestClient(app) as client:
        client.app.state.openemr_client = SimpleNamespace(
            get_fhir_metadata=AsyncMock(return_value=capability),
        )
        resp = client.get("/api/fhir/metadata")

    assert resp.status_code == 200
    assert resp.json()["resourceType"] == "CapabilityStatement"
    assert resp.json()["fhirVersion"] == "4.0.1"


# ------------------------------------------------------------------
# Session summary preview truncation
# ------------------------------------------------------------------


def test_session_summary_preview_truncated_at_60_chars() -> None:
    """The first_message_preview in session summaries is at most 60 chars."""
    long_message = "A" * 200  # 200 character message
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        client.post(
            "/api/chat",
            headers=_headers("u-trunc"),
            json={"message": long_message},
        )
        sessions = client.get("/api/sessions", headers=_headers("u-trunc")).json()

    assert len(sessions) >= 1
    preview = sessions[0]["first_message_preview"]
    assert len(preview) <= 60, f"Preview should be at most 60 chars, got {len(preview)}"


# ------------------------------------------------------------------
# Navigate-to-patient in ChatResponse
# ------------------------------------------------------------------


def test_chat_response_includes_navigate_to_patient_when_set() -> None:
    """When agent sets navigate_to_patient, it appears in ChatResponse once."""
    class _NavigateAgentLoop:
        async def run(self, session: AgentSession, user_message: str) -> AgentSession:
            session.messages.append(AgentMessage(role="assistant", content="Opening chart"))
            session.navigate_to_patient = {"pid": "42", "name": "Jane Doe"}
            return session

    with TestClient(app) as client:
        client.app.state.agent_loop = _NavigateAgentLoop()
        resp = client.post(
            "/api/chat",
            headers=_headers("u-nav"),
            json={"message": "open chart for Jane Doe"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["navigate_to_patient"] is not None
    assert body["navigate_to_patient"]["pid"] == "42"


def test_chat_response_navigate_to_patient_is_consumed() -> None:
    """navigate_to_patient is a one-shot signal — cleared after first response."""
    class _NavigateAgentLoop:
        _called = False

        async def run(self, session: AgentSession, user_message: str) -> AgentSession:
            session.messages.append(AgentMessage(role="assistant", content="done"))
            if not self._called:
                session.navigate_to_patient = {"pid": "42"}
                self.__class__._called = True
            return session

    with TestClient(app) as client:
        client.app.state.agent_loop = _NavigateAgentLoop()
        resp1 = client.post(
            "/api/chat", headers=_headers("u-nav2"), json={"message": "open chart"},
        )
        sid = resp1.json()["session_id"]

        # Second message — navigate_to_patient should NOT be in the response again
        resp2 = client.post(
            "/api/chat",
            headers=_headers("u-nav2"),
            json={"session_id": sid, "message": "follow up"},
        )

    assert resp1.json()["navigate_to_patient"] is not None
    assert resp2.json()["navigate_to_patient"] is None


# ------------------------------------------------------------------
# Session summary uses 'name' key as fallback for patient_name
# ------------------------------------------------------------------


def test_session_summary_uses_name_key_as_patient_name_fallback() -> None:
    """visible_data 'name' key is used as patient_name when 'patient_name' is absent."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        client.post(
            "/api/chat",
            headers=_headers("u-name-key"),
            json={
                "message": "hello",
                "page_context": {
                    "patient_id": "77",
                    "visible_data": {"name": "John Smith"},  # uses 'name' not 'patient_name'
                },
            },
        )
        sessions = client.get("/api/sessions?patient_id=77", headers=_headers("u-name-key")).json()

    assert len(sessions) >= 1
    assert sessions[0]["patient_name"] == "John Smith"


# ------------------------------------------------------------------
# POST /api/sessions returns valid structure
# ------------------------------------------------------------------


def test_create_session_returns_session_id_and_phase() -> None:
    """POST /api/sessions returns session_id and planning phase."""
    with TestClient(app) as client:
        resp = client.post("/api/sessions", headers=_headers("u-create"))

    assert resp.status_code == 200
    body = resp.json()
    assert "session_id" in body
    assert len(body["session_id"]) > 0
    assert body["phase"] == "planning"


def test_create_session_requires_auth() -> None:
    """POST /api/sessions returns 401 without auth header."""
    with TestClient(app) as client:
        resp = client.post("/api/sessions")  # no headers

    assert resp.status_code == 401


# ------------------------------------------------------------------
# GET messages includes openemr_pid when set
# ------------------------------------------------------------------


def test_get_session_messages_includes_openemr_pid() -> None:
    """GET /messages response includes openemr_pid from session."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        created = client.post("/api/sessions", headers=_headers("u-pid-msg")).json()
        session = _ephemeral_sessions[created["session_id"]]
        session.openemr_pid = "42"

        msgs = client.get(
            f"/api/sessions/{created['session_id']}/messages",
            headers=_headers("u-pid-msg"),
        ).json()

    assert msgs["openemr_pid"] == "42"


# ------------------------------------------------------------------
# Execute manifest skips rejected items
# ------------------------------------------------------------------


def test_execute_manifest_rejected_items_shown_with_rejected_status() -> None:
    """Items with status='rejected' are included in response with rejected status (not executed)."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _ToolCallingAgentLoop()
        created = client.post("/api/sessions", headers=_headers("u-reject-exec")).json()
        session = _ephemeral_sessions[created["session_id"]]
        session.phase = "reviewing"
        session.manifest = ChangeManifest(
            patient_id=PATIENT_FHIR_UUID,
            items=[
                ManifestItem(
                    id="approved-item",
                    resource_type="Condition",
                    action=ManifestAction.CREATE,
                    proposed_value={"code": "E11.9"},
                    source_reference="Encounter/aaa",
                    description="Add diabetes",
                    status="approved",
                ),
                ManifestItem(
                    id="rejected-item",
                    resource_type="Condition",
                    action=ManifestAction.CREATE,
                    proposed_value={"code": "I10"},
                    source_reference="Encounter/aaa",
                    description="Add hypertension",
                    status="rejected",
                ),
            ],
        )

        resp = client.post(
            f"/api/manifest/{created['session_id']}/execute",
            headers=_headers("u-reject-exec"),
        )

    assert resp.status_code == 200
    items = resp.json()["items"]
    # Both items appear; rejected keeps its status
    item_map = {i["id"]: i for i in items}
    assert "approved-item" in item_map
    assert "rejected-item" in item_map
    assert item_map["rejected-item"]["status"] == "rejected"


# ------------------------------------------------------------------
# Session list returns correct metadata fields
# ------------------------------------------------------------------


def test_session_list_response_has_expected_fields() -> None:
    """GET /api/sessions returns all expected metadata fields."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        client.post(
            "/api/chat",
            headers=_headers("u-list-fields"),
            json={"message": "hello there"},
        )
        sessions = client.get("/api/sessions", headers=_headers("u-list-fields")).json()

    assert len(sessions) >= 1
    session = sessions[0]
    assert "session_id" in session
    assert "phase" in session
    assert "message_count" in session
    assert "created_at" in session
    assert "first_message_preview" in session
    assert "patient_name" in session
    assert "patient_id" in session


def test_session_list_empty_when_no_sessions() -> None:
    """GET /api/sessions returns empty list for a user with no sessions."""
    with TestClient(app) as client:
        sessions = client.get("/api/sessions", headers=_headers("u-empty-list")).json()

    assert sessions == []


# ------------------------------------------------------------------
# _session_summary — visible_data non-dict → patient_name is None
# ------------------------------------------------------------------


def test_session_summary_patient_name_none_when_visible_data_is_list() -> None:
    """When visible_data is a list (not dict), _session_summary returns patient_name=None."""
    from src.agent.models import PageContext
    from src.api.main import _session_summary

    session = AgentSession()
    # Bypass Pydantic validation to simulate legacy sessions with non-dict visible_data
    session.page_context = PageContext.model_construct(
        patient_id="33",
        visible_data=["item1", "item2"],  # list, not dict
    )
    summary = _session_summary(session)
    assert summary["patient_name"] is None
    assert summary["patient_id"] == "33"


def test_session_summary_patient_name_none_when_visible_data_is_string() -> None:
    """When visible_data is a string (not dict), _session_summary returns patient_name=None."""
    from src.agent.models import PageContext
    from src.api.main import _session_summary

    session = AgentSession()
    session.page_context = PageContext.model_construct(
        patient_id="34",
        visible_data="some-string-value",  # string, not dict
    )
    summary = _session_summary(session)
    assert summary["patient_name"] is None
    assert summary["patient_id"] == "34"


# ------------------------------------------------------------------
# _summarize_tool_calls — None when no tool calls
# ------------------------------------------------------------------


def test_chat_response_tool_calls_summary_is_null_when_no_tool_calls() -> None:
    """tool_calls_summary is null in chat response when agent makes no tool calls."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        resp = client.post(
            "/api/chat",
            headers=_headers("u-no-tools"),
            json={"message": "simple question"},
        )

    assert resp.status_code == 200
    assert resp.json()["tool_calls_summary"] is None


# ------------------------------------------------------------------
# _session_summary — patient_id None when no page_context
# ------------------------------------------------------------------


def test_session_summary_patient_id_none_when_no_page_context() -> None:
    """When session has no page_context, session summary patient_id is None."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        client.post(
            "/api/chat",
            headers=_headers("u-no-ctx"),
            json={"message": "question without context"},
        )
        sessions = client.get("/api/sessions", headers=_headers("u-no-ctx")).json()

    assert len(sessions) >= 1
    # Sessions without page_context should have null patient_id
    no_ctx_sessions = [s for s in sessions if s.get("patient_id") is None]
    assert len(no_ctx_sessions) >= 1


# ------------------------------------------------------------------
# approve_manifest — non-dict proposed_value filtered out
# ------------------------------------------------------------------


def test_approve_manifest_non_dict_proposed_value_is_filtered() -> None:
    """modified_items with proposed_value that is not a dict are ignored."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        client.app.state.openemr_client = SimpleNamespace(
            get_fhir_metadata=AsyncMock(return_value={}),
            fhir_read=AsyncMock(return_value={"resourceType": "Bundle", "total": 0, "entry": []}),
        )
        created = client.post("/api/sessions", headers=_headers("u-non-dict")).json()
        session = _ephemeral_sessions[created["session_id"]]
        original_proposed = {"code": "E11.9"}
        session.manifest = ChangeManifest(
            patient_id=PATIENT_FHIR_UUID,
            items=[
                ManifestItem(
                    id="nd-item-1",
                    resource_type="Condition",
                    action=ManifestAction.CREATE,
                    proposed_value=original_proposed,
                    source_reference="Encounter/enc-1",
                    description="diabetes",
                )
            ],
        )
        client.app.state.session_store.save(session)

        # proposed_value is a list — should be filtered out (not a dict)
        response = client.post(
            f"/api/manifest/{session.id}/approve",
            headers=_headers("u-non-dict"),
            json={
                "approved_items": ["nd-item-1"],
                "modified_items": [
                    {"id": "nd-item-1", "proposed_value": ["I10", "Hypertension"]}
                ],
            },
        )

        assert response.status_code == 200
        updated = client.app.state.session_store.load(session.id, "u-non-dict")

    # Since proposed_value was not a dict, the modification is ignored;
    # the item keeps its original proposed_value
    assert updated.manifest is not None
    assert updated.manifest.items[0].proposed_value == original_proposed


# ------------------------------------------------------------------
# approve_manifest — mixed approval/rejection/pending
# ------------------------------------------------------------------


def test_approve_manifest_mixed_items_sets_pending_for_unreviewed() -> None:
    """Items not in approved_items or rejected_items get status='pending'."""
    with TestClient(app) as client:
        client.app.state.agent_loop = _DummyAgentLoop()
        client.app.state.openemr_client = SimpleNamespace(
            get_fhir_metadata=AsyncMock(return_value={}),
            fhir_read=AsyncMock(return_value={"resourceType": "Bundle", "total": 0, "entry": []}),
        )
        created = client.post("/api/sessions", headers=_headers("u-mixed")).json()
        session = _ephemeral_sessions[created["session_id"]]
        session.manifest = ChangeManifest(
            patient_id=PATIENT_FHIR_UUID,
            items=[
                ManifestItem(
                    id="mix-item-1",
                    resource_type="Condition",
                    action=ManifestAction.CREATE,
                    proposed_value={"code": "E11.9"},
                    source_reference="Encounter/enc-1",
                    description="diabetes",
                ),
                ManifestItem(
                    id="mix-item-2",
                    resource_type="Condition",
                    action=ManifestAction.CREATE,
                    proposed_value={"code": "I10"},
                    source_reference="Encounter/enc-1",
                    description="hypertension",
                ),
                ManifestItem(
                    id="mix-item-3",
                    resource_type="MedicationRequest",
                    action=ManifestAction.CREATE,
                    proposed_value={"drug": "Aspirin"},
                    source_reference="Encounter/enc-1",
                    description="aspirin",
                ),
            ],
        )
        client.app.state.session_store.save(session)

        response = client.post(
            f"/api/manifest/{session.id}/approve",
            headers=_headers("u-mixed"),
            json={
                "approved_items": ["mix-item-1"],
                "rejected_items": ["mix-item-2"],
                # mix-item-3 is neither approved nor rejected → "pending"
            },
        )

        assert response.status_code == 200
        updated = client.app.state.session_store.load(session.id, "u-mixed")

    items_by_id = {i.id: i for i in updated.manifest.items}
    assert items_by_id["mix-item-1"].status == "approved"
    assert items_by_id["mix-item-2"].status == "rejected"
    assert items_by_id["mix-item-3"].status == "pending"
