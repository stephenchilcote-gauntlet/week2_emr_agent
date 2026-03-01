from __future__ import annotations

from pathlib import Path

from src.agent.models import AgentMessage, AgentSession, ChangeManifest, ManifestAction, ManifestItem
from src.api.session_store import SessionStore


def test_session_store_round_trip(tmp_path: Path) -> None:
    store = SessionStore(str(tmp_path / "sessions.db"))
    session = AgentSession(openemr_user_id="user-1")
    session.messages.append(AgentMessage(role="user", content="hello"))
    session.messages.append(AgentMessage(role="assistant", content="world"))
    session.manifest = ChangeManifest(
        patient_id="bbb13f7a-966e-4c7c-aea5-4bac3ce98505",
        items=[
            ManifestItem(
                id="item-1",
                resource_type="Condition",
                action=ManifestAction.CREATE,
                proposed_value={"code": "E11.9"},
                source_reference="Encounter/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                description="Add diagnosis",
                execution_result="ok",
            )
        ],
    )

    store.save(session)
    loaded = store.load(session.id, session.openemr_user_id)

    assert loaded is not None
    assert loaded.id == session.id
    assert loaded.openemr_user_id == "user-1"
    assert len(loaded.messages) == 2
    assert loaded.manifest is not None
    assert loaded.manifest.items[0].execution_result == "ok"


def test_session_store_filters_by_user(tmp_path: Path) -> None:
    store = SessionStore(str(tmp_path / "sessions.db"))
    s1 = AgentSession(openemr_user_id="user-a")
    s2 = AgentSession(openemr_user_id="user-b")
    store.save(s1)
    store.save(s2)

    user_a_sessions = store.list_for_user("user-a")

    assert [session.id for session in user_a_sessions] == [s1.id]
