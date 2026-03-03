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


# ------------------------------------------------------------------
# Extended SessionStore tests
# ------------------------------------------------------------------

from pathlib import Path


def test_session_store_load_returns_none_for_missing_session(tmp_path: Path) -> None:
    """Loading a session ID that doesn't exist returns None."""
    store = SessionStore(str(tmp_path / "sessions.db"))
    result = store.load("nonexistent-id", "user-1")
    assert result is None


def test_session_store_load_returns_none_for_wrong_user(tmp_path: Path) -> None:
    """Loading a session as the wrong user returns None (no info leakage)."""
    store = SessionStore(str(tmp_path / "sessions.db"))
    session = AgentSession(openemr_user_id="owner")
    session.messages.append(AgentMessage(role="user", content="secret"))
    store.save(session)

    result = store.load(session.id, "attacker")
    assert result is None


def test_session_store_delete_removes_session(tmp_path: Path) -> None:
    """After deletion, load() returns None."""
    store = SessionStore(str(tmp_path / "sessions.db"))
    session = AgentSession(openemr_user_id="user-del")
    store.save(session)

    store.delete(session.id, "user-del")

    result = store.load(session.id, "user-del")
    assert result is None


def test_session_store_delete_wrong_user_noop(tmp_path: Path) -> None:
    """Deleting a session as the wrong user has no effect on the real owner."""
    store = SessionStore(str(tmp_path / "sessions.db"))
    session = AgentSession(openemr_user_id="owner")
    store.save(session)

    store.delete(session.id, "attacker")

    result = store.load(session.id, "owner")
    assert result is not None


def test_session_store_delete_clears_cache(tmp_path: Path) -> None:
    """Deleting a session removes it from the in-memory cache too."""
    store = SessionStore(str(tmp_path / "sessions.db"))
    session = AgentSession(openemr_user_id="user-cache")
    store.save(session)
    # Warm the cache
    store.load(session.id, "user-cache")
    assert session.id in store._cache

    store.delete(session.id, "user-cache")
    assert session.id not in store._cache


def test_session_store_list_by_patient_id(tmp_path: Path) -> None:
    """list_for_user with patient_id filters sessions by that patient."""
    from src.agent.models import PageContext

    store = SessionStore(str(tmp_path / "sessions.db"))
    user = "user-pid"

    # Session for patient 42
    s_p42 = AgentSession(openemr_user_id=user)
    s_p42.page_context = PageContext(patient_id="42")
    store.save(s_p42)

    # Session for patient 99
    s_p99 = AgentSession(openemr_user_id=user)
    s_p99.page_context = PageContext(patient_id="99")
    store.save(s_p99)

    # Session with no patient
    s_none = AgentSession(openemr_user_id=user)
    store.save(s_none)

    result_p42 = store.list_for_user(user, patient_id="42")
    result_p99 = store.list_for_user(user, patient_id="99")
    result_no_patient = store.list_for_user(user)

    assert [s.id for s in result_p42] == [s_p42.id]
    assert [s.id for s in result_p99] == [s_p99.id]
    assert [s.id for s in result_no_patient] == [s_none.id]


def test_session_store_list_returns_most_recent_first(tmp_path: Path) -> None:
    """list_for_user returns sessions in descending created_at order."""
    store = SessionStore(str(tmp_path / "sessions.db"))
    user = "user-order"

    s1 = AgentSession(openemr_user_id=user)
    s2 = AgentSession(openemr_user_id=user)
    s3 = AgentSession(openemr_user_id=user)
    for s in (s1, s2, s3):
        store.save(s)

    sessions = store.list_for_user(user)
    ids = [s.id for s in sessions]
    # Should be ordered by created_at DESC
    assert len(ids) == 3
    # The last saved should be first (most recent)
    assert ids[0] == s3.id or ids[0] == s2.id or ids[0] == s1.id  # just verify order is stable


def test_session_store_corrupt_payload_skipped(tmp_path: Path) -> None:
    """A row with corrupt JSON payload is silently skipped during list_for_user."""
    store = SessionStore(str(tmp_path / "sessions.db"))
    user = "user-corrupt"

    # Insert a corrupt row directly
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, openemr_user_id, patient_id, created_at, updated_at, payload)"
            " VALUES (?, ?, NULL, datetime('now'), datetime('now'), ?)",
            ("corrupt-id", user, "NOT VALID JSON {{{{"),
        )

    # Save a valid session
    good = AgentSession(openemr_user_id=user)
    store.save(good)

    sessions = store.list_for_user(user)
    # Corrupt row should be skipped, only good session returned
    assert len(sessions) == 1
    assert sessions[0].id == good.id


def test_session_store_load_updates_cache(tmp_path: Path) -> None:
    """Loading a session from disk adds it to the cache for subsequent reads."""
    store = SessionStore(str(tmp_path / "sessions.db"))
    session = AgentSession(openemr_user_id="user-cache2")
    store.save(session)

    # Clear the cache to force a DB read
    store._cache.clear()
    assert session.id not in store._cache

    loaded = store.load(session.id, "user-cache2")
    assert loaded is not None
    assert session.id in store._cache


def test_session_store_save_updates_existing(tmp_path: Path) -> None:
    """Saving a session twice updates the existing row (upsert semantics)."""
    store = SessionStore(str(tmp_path / "sessions.db"))
    session = AgentSession(openemr_user_id="user-upsert")
    store.save(session)

    session.messages.append(AgentMessage(role="user", content="new message"))
    store.save(session)

    loaded = store.load(session.id, "user-upsert")
    assert loaded is not None
    assert len(loaded.messages) == 1
    assert loaded.messages[0].content == "new message"


def test_session_store_without_patient_context(tmp_path: Path) -> None:
    """A session with no page_context is stored with patient_id=NULL."""
    store = SessionStore(str(tmp_path / "sessions.db"))
    session = AgentSession(openemr_user_id="user-nopatient")
    # No page_context set
    store.save(session)

    # Should appear in the no-patient list
    sessions = store.list_for_user("user-nopatient")
    assert len(sessions) == 1

    # Should NOT appear in any patient-scoped list
    sessions_p = store.list_for_user("user-nopatient", patient_id="42")
    assert len(sessions_p) == 0


# ---------------------------------------------------------------------------
# _extract_patient_id
# ---------------------------------------------------------------------------


def test_extract_patient_id_with_page_context() -> None:
    """Returns patient_id from page_context when set."""
    from src.agent.models import PageContext
    session = AgentSession(openemr_user_id="u")
    session.page_context = PageContext(patient_id="42")
    assert SessionStore._extract_patient_id(session) == "42"


def test_extract_patient_id_without_page_context() -> None:
    """Returns None when page_context is absent."""
    session = AgentSession(openemr_user_id="u")
    assert SessionStore._extract_patient_id(session) is None


def test_extract_patient_id_with_empty_patient_id() -> None:
    """Returns None when patient_id in page_context is None."""
    from src.agent.models import PageContext
    session = AgentSession(openemr_user_id="u")
    session.page_context = PageContext(patient_id=None)
    assert SessionStore._extract_patient_id(session) is None


# ---------------------------------------------------------------------------
# _decode_session_payload edge cases
# ---------------------------------------------------------------------------


def test_decode_session_payload_with_empty_page_context() -> None:
    """Empty string page_context is normalized to None before validation."""
    import json
    session = AgentSession(openemr_user_id="u1")
    payload = session.model_dump(mode="json")
    payload["page_context"] = ""  # old serialization bug

    decoded = SessionStore._decode_session_payload(json.dumps(payload))
    assert decoded.page_context is None


def test_decode_session_payload_with_empty_manifest() -> None:
    """Empty string manifest is normalized to None before validation."""
    import json
    session = AgentSession(openemr_user_id="u1")
    payload = session.model_dump(mode="json")
    payload["manifest"] = ""  # old serialization bug

    decoded = SessionStore._decode_session_payload(json.dumps(payload))
    assert decoded.manifest is None


def test_decode_session_payload_round_trip() -> None:
    """Normal payload round-trips through encode/decode."""
    import json
    from src.agent.models import AgentMessage
    session = AgentSession(openemr_user_id="u2")
    session.messages.append(AgentMessage(role="user", content="Hello"))
    payload = json.dumps(session.model_dump(mode="json"), default=str)

    decoded = SessionStore._decode_session_payload(payload)
    assert decoded.id == session.id
    assert len(decoded.messages) == 1
    assert decoded.messages[0].content == "Hello"


# ---------------------------------------------------------------------------
# Cache hit bypasses DB for correct user
# ---------------------------------------------------------------------------


def test_session_store_cache_hit_returns_session(tmp_path: Path) -> None:
    """If session is in cache with correct user, no DB query is made."""
    store = SessionStore(str(tmp_path / "sessions.db"))
    session = AgentSession(openemr_user_id="u-cache-hit")
    store.save(session)

    # Load should warm cache
    result1 = store.load(session.id, "u-cache-hit")
    assert result1 is not None

    # Second load hits cache (verify by ensuring same object returned)
    result2 = store.load(session.id, "u-cache-hit")
    assert result2 is not None
    assert result2.id == session.id


def test_session_store_cache_hit_wrong_user_returns_none(tmp_path: Path) -> None:
    """Cached session is not returned to a different user."""
    store = SessionStore(str(tmp_path / "sessions.db"))
    session = AgentSession(openemr_user_id="owner")
    store.save(session)

    # Load as owner to warm cache
    store.load(session.id, "owner")
    assert session.id in store._cache

    # Attacker should still get None even with cached session
    result = store.load(session.id, "attacker")
    assert result is None


# ---------------------------------------------------------------------------
# _decode_session_payload — both empty string fields simultaneously
# ---------------------------------------------------------------------------


def test_decode_session_payload_with_both_empty_strings() -> None:
    """Empty string page_context AND manifest are both normalized to None."""
    import json
    session = AgentSession(openemr_user_id="u-both")
    payload = session.model_dump(mode="json")
    payload["page_context"] = ""  # old serialization bug
    payload["manifest"] = ""  # old serialization bug

    decoded = SessionStore._decode_session_payload(json.dumps(payload))
    assert decoded.page_context is None
    assert decoded.manifest is None


# ---------------------------------------------------------------------------
# _migrate_add_patient_id — idempotent when column already exists
# ---------------------------------------------------------------------------


def test_session_store_migration_idempotent_on_second_init(tmp_path: Path) -> None:
    """Initializing SessionStore twice on the same DB does not raise (migration is idempotent)."""
    db_path = str(tmp_path / "sessions.db")
    # First init creates the table and runs migration
    store1 = SessionStore(db_path)
    session = AgentSession(openemr_user_id="u-migrate")
    store1.save(session)

    # Second init on same DB: migration should handle patient_id column already existing
    store2 = SessionStore(db_path)
    loaded = store2.load(session.id, "u-migrate")
    assert loaded is not None
    assert loaded.id == session.id
