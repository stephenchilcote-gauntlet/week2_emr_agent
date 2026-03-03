from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from scripts.session_trace import (
    _extract_key_tags,
    _fetch_jaeger_traces,
    _format_audit_events,
    _format_conversation,
    _format_header,
    _format_jaeger_traces,
    _format_manifest,
    _format_page_context,
    _load_session,
    _load_session_from_api,
    _load_audit_from_api,
    _parent_span_id,
    _trace_start,
    main,
)


def _seed_db(db_path: Path, session_id: str, payload: dict) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions "
        "(id TEXT PRIMARY KEY, openemr_user_id TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, payload TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
        (session_id, "user-1", "2026-01-01", "2026-01-01", json.dumps(payload)),
    )
    conn.commit()
    conn.close()


SESSION_PAYLOAD = {
    "id": "test-session-123",
    "openemr_user_id": "doc-smith",
    "phase": "reviewing",
    "created_at": "2026-01-15T10:30:00",
    "fhir_patient_id": "fhir-pat-1",
    "page_context": {
        "patient_id": "pid-1",
        "encounter_id": "enc-1",
        "page_type": "demographics",
        "visible_data": {"patient_name": "Maria Santos"},
    },
    "messages": [
        {"role": "user", "content": "What meds is this patient on?"},
        {
            "role": "assistant",
            "content": "Let me look that up.",
            "tool_calls": [
                {
                    "id": "tc-1",
                    "name": "fhir_read",
                    "arguments": {"resource_type": "MedicationRequest"},
                }
            ],
        },
        {
            "role": "tool",
            "content": "",
            "tool_results": [
                {
                    "tool_call_id": "tc-1",
                    "content": '{"resourceType": "Bundle", "total": 1}',
                    "is_error": False,
                }
            ],
        },
        {"role": "assistant", "content": "The patient is taking Metformin."},
    ],
    "manifest": {
        "id": "manifest-1",
        "patient_id": "pid-1",
        "encounter_id": "enc-1",
        "status": "draft",
        "items": [
            {
                "id": "item-1",
                "action": "create",
                "resource_type": "Condition",
                "description": "Add hypertension",
                "status": "approved",
                "confidence": "high",
                "execution_result": None,
            }
        ],
    },
}


def test_load_session_from_db(tmp_path: Path) -> None:
    db = tmp_path / "sessions.db"
    _seed_db(db, "sess-1", {"id": "sess-1", "phase": "planning"})

    result = _load_session(str(db), "sess-1")
    assert result is not None
    assert result["id"] == "sess-1"


def test_load_session_missing(tmp_path: Path) -> None:
    db = tmp_path / "sessions.db"
    _seed_db(db, "sess-1", {"id": "sess-1"})

    assert _load_session(str(db), "nonexistent") is None


def test_load_session_no_db() -> None:
    assert _load_session("/tmp/does_not_exist_xyz.db", "sess-1") is None


def test_format_header() -> None:
    md = _format_header(SESSION_PAYLOAD)
    assert "doc-smith" in md
    assert "reviewing" in md
    assert "fhir-pat-1" in md


def test_format_page_context() -> None:
    md = _format_page_context(SESSION_PAYLOAD["page_context"])
    assert "pid-1" in md
    assert "enc-1" in md
    assert "Maria Santos" in md


def test_format_page_context_none() -> None:
    assert _format_page_context(None) == ""


def test_format_conversation() -> None:
    md = _format_conversation(SESSION_PAYLOAD["messages"])
    assert "👤 User" in md
    assert "What meds" in md
    assert "🤖 Assistant" in md
    assert "fhir_read" in md
    assert "🔧 Tool Results" in md
    assert "✅" in md
    assert "Metformin" in md


def test_format_conversation_empty() -> None:
    md = _format_conversation([])
    assert "No messages" in md


def test_format_conversation_truncates_long_results() -> None:
    messages = [
        {
            "role": "tool",
            "content": "",
            "tool_results": [
                {
                    "tool_call_id": "tc-x",
                    "content": "x" * 2000,
                    "is_error": False,
                }
            ],
        }
    ]
    md = _format_conversation(messages)
    assert "truncated" in md


def test_format_conversation_error_result() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "tc-err", "name": "openemr_api", "arguments": {}}],
        },
        {
            "role": "tool",
            "content": "",
            "tool_results": [
                {"tool_call_id": "tc-err", "content": "connection refused", "is_error": True}
            ],
        },
    ]
    md = _format_conversation(messages)
    assert "❌" in md
    assert "openemr_api" in md


def test_format_manifest() -> None:
    md = _format_manifest(SESSION_PAYLOAD["manifest"])
    assert "manifest-1" in md
    assert "Condition" in md
    assert "hypertension" in md
    assert "approved" in md


def test_format_manifest_none() -> None:
    assert _format_manifest(None) == ""


JAEGER_TRACE = {
    "traceID": "abcdef1234567890",
    "spans": [
        {
            "spanID": "span-root",
            "operationName": "POST /api/chat",
            "startTime": 1706000000000000,
            "duration": 5000000,
            "references": [],
            "tags": [
                {"key": "session.id", "type": "string", "value": "sess-1"},
                {"key": "http.status_code", "type": "int64", "value": 200},
            ],
            "logs": [],
        },
        {
            "spanID": "span-llm",
            "operationName": "llm._call_llm",
            "startTime": 1706000000100000,
            "duration": 3000000,
            "references": [
                {"refType": "CHILD_OF", "traceID": "abcdef1234567890", "spanID": "span-root"}
            ],
            "tags": [
                {"key": "llm.model", "type": "string", "value": "claude-sonnet-4-20250514"},
                {"key": "llm.input_tokens", "type": "int64", "value": 1500},
                {"key": "llm.output_tokens", "type": "int64", "value": 300},
            ],
            "logs": [],
        },
        {
            "spanID": "span-tool",
            "operationName": "tool._execute_tool",
            "startTime": 1706000003200000,
            "duration": 400000,
            "references": [
                {"refType": "CHILD_OF", "traceID": "abcdef1234567890", "spanID": "span-root"}
            ],
            "tags": [
                {"key": "tool.name", "type": "string", "value": "fhir_read"},
                {"key": "tool.success", "type": "bool", "value": True},
            ],
            "logs": [],
        },
    ],
    "processes": {"p1": {"serviceName": "openemr-agent", "tags": []}},
}


def test_format_jaeger_traces() -> None:
    md = _format_jaeger_traces([JAEGER_TRACE])
    assert "**1** trace(s)" in md
    assert "abcdef1234567890" in md
    assert "POST /api/chat" in md
    assert "llm._call_llm" in md
    assert "claude-sonnet-4-20250514" in md
    assert "fhir_read" in md
    assert "5000ms" in md


def test_format_jaeger_traces_nests_children() -> None:
    md = _format_jaeger_traces([JAEGER_TRACE])
    lines = md.split("\n")
    llm_line = next(l for l in lines if "llm._call_llm" in l)
    root_line = next(l for l in lines if "POST /api/chat" in l)
    # Child span should be indented relative to root
    assert llm_line.startswith("  ")
    assert not root_line.startswith("  ")


def test_fetch_jaeger_traces_connection_error() -> None:
    result = _fetch_jaeger_traces("http://localhost:1", "fake-id")
    assert result == []


def test_main_end_to_end(capsys) -> None:
    with patch("scripts.session_trace._load_session_from_api", return_value=SESSION_PAYLOAD):
        with patch("scripts.session_trace._fetch_jaeger_traces", return_value=[JAEGER_TRACE]):
            with patch("sys.argv", ["session_trace", "test-session-123"]):
                main()

    output = capsys.readouterr().out
    assert "# Session Trace: `test-session-123`" in output
    assert "## Session Info" in output
    assert "## Conversation" in output
    assert "## Jaeger Traces" in output
    assert "What meds" in output
    assert "POST /api/chat" in output


def test_main_db_only(capsys) -> None:
    payload = {"id": "sess-db", "phase": "planning", "messages": []}

    with patch("scripts.session_trace._load_session_from_api", return_value=payload):
        with patch("scripts.session_trace._fetch_jaeger_traces", return_value=[]):
            with patch("sys.argv", ["session_trace", "sess-db"]):
                main()

    output = capsys.readouterr().out
    assert "No traces found in Jaeger" in output


def test_main_jaeger_only(capsys) -> None:
    with patch("scripts.session_trace._load_session_from_api", return_value=None):
        with patch("scripts.session_trace._fetch_jaeger_traces", return_value=[JAEGER_TRACE]):
            with patch("sys.argv", ["session_trace", "missing-id"]):
                main()

    output = capsys.readouterr().out
    assert "not found in database" in output
    assert "Jaeger Traces" in output


def test_main_no_data() -> None:
    with patch("scripts.session_trace._load_session_from_api", return_value=None):
        with patch("scripts.session_trace._fetch_jaeger_traces", return_value=[]):
            with patch("sys.argv", ["session_trace", "ghost"]):
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _format_audit_events
# ---------------------------------------------------------------------------


def test_format_audit_events_empty_list() -> None:
    """Empty events list still produces a valid markdown table header."""
    md = _format_audit_events([])
    assert "Audit Trail" in md
    assert "0" in md
    assert "| # |" in md


def test_format_audit_events_single_event() -> None:
    """Single event appears in the markdown table."""
    events = [
        {
            "timestamp": "2026-03-03T12:34:56Z",
            "event_type": "chat_received",
            "summary": "User message received",
            "details": {"message_length": 42},
        }
    ]
    md = _format_audit_events(events)
    assert "chat_received" in md
    assert "User message received" in md
    assert "12:34:56" in md  # time extracted from ISO timestamp
    assert "💬" in md  # AUDIT_ICONS["chat_received"]


def test_format_audit_events_feedback_up_icon() -> None:
    """message_feedback event with rating='up' shows thumbs-up icon."""
    events = [
        {
            "timestamp": "2026-03-03T10:00:00Z",
            "event_type": "message_feedback",
            "summary": "User rated message 0 as up",
            "details": {"message_index": 0, "rating": "up"},
        }
    ]
    md = _format_audit_events(events)
    assert "👍" in md


def test_format_audit_events_feedback_down_icon() -> None:
    """message_feedback event with rating='down' shows thumbs-down icon."""
    events = [
        {
            "timestamp": "2026-03-03T10:00:00Z",
            "event_type": "message_feedback",
            "summary": "User rated message 0 as down",
            "details": {"message_index": 0, "rating": "down"},
        }
    ]
    md = _format_audit_events(events)
    assert "👎" in md


def test_format_audit_events_details_truncated_at_80() -> None:
    """Detail strings longer than 80 chars are truncated with ellipsis."""
    long_detail = "key=" + "x" * 90  # over 80 chars
    events = [
        {
            "timestamp": "2026-03-03T10:00:00Z",
            "event_type": "manifest_reviewed",
            "summary": "Manifest reviewed",
            "details": {"very_long_key": "x" * 90},
        }
    ]
    md = _format_audit_events(events)
    assert "…" in md


def test_format_audit_events_unknown_event_type_uses_default_icon() -> None:
    """Unknown event types use the default 📝 icon."""
    events = [
        {
            "timestamp": "2026-03-03T10:00:00Z",
            "event_type": "assistant_responded",
            "summary": "Response sent",
            "details": {},
        }
    ]
    md = _format_audit_events(events)
    assert "📝" in md


# ---------------------------------------------------------------------------
# _extract_key_tags
# ---------------------------------------------------------------------------


def test_extract_key_tags_returns_known_tags() -> None:
    """Known tags are returned as key=value strings."""
    span = {
        "tags": [
            {"key": "session.id", "type": "string", "value": "sess-1"},
            {"key": "tool.name", "type": "string", "value": "fhir_read"},
            {"key": "unknown.key", "type": "string", "value": "ignored"},
        ]
    }
    attrs = _extract_key_tags(span)
    assert "session.id=sess-1" in attrs
    assert "tool.name=fhir_read" in attrs
    assert len(attrs) == 2  # unknown.key not included


def test_extract_key_tags_llm_latency_formatted_with_ms() -> None:
    """llm.latency_ms is formatted as a float with 'ms' suffix."""
    span = {
        "tags": [
            {"key": "llm.latency_ms", "type": "float", "value": 1234.5},
        ]
    }
    attrs = _extract_key_tags(span)
    assert len(attrs) == 1
    assert "ms" in attrs[0]
    assert "1235" in attrs[0] or "1234" in attrs[0]


def test_extract_key_tags_empty_span() -> None:
    """Span with no tags returns empty list."""
    attrs = _extract_key_tags({"tags": []})
    assert attrs == []


def test_extract_key_tags_no_tags_key() -> None:
    """Span without 'tags' key returns empty list."""
    attrs = _extract_key_tags({})
    assert attrs == []


# ---------------------------------------------------------------------------
# _parent_span_id
# ---------------------------------------------------------------------------


def test_parent_span_id_returns_child_of_span() -> None:
    """Returns the spanID from a CHILD_OF reference."""
    span = {
        "references": [
            {"refType": "CHILD_OF", "traceID": "abc", "spanID": "parent-span-123"},
        ]
    }
    assert _parent_span_id(span) == "parent-span-123"


def test_parent_span_id_returns_none_when_no_child_of() -> None:
    """Returns None when no CHILD_OF reference exists."""
    span = {
        "references": [
            {"refType": "FOLLOWS_FROM", "traceID": "abc", "spanID": "other"},
        ]
    }
    assert _parent_span_id(span) is None


def test_parent_span_id_returns_none_when_no_references() -> None:
    """Returns None when references list is empty."""
    assert _parent_span_id({"references": []}) is None


def test_parent_span_id_returns_none_when_key_missing() -> None:
    """Returns None when span has no 'references' key."""
    assert _parent_span_id({}) is None


# ---------------------------------------------------------------------------
# _trace_start
# ---------------------------------------------------------------------------


def test_trace_start_returns_minimum_start_time() -> None:
    """Returns the minimum startTime across all spans."""
    trace = {
        "spans": [
            {"startTime": 1000},
            {"startTime": 500},
            {"startTime": 2000},
        ]
    }
    assert _trace_start(trace) == 500


def test_trace_start_returns_zero_when_no_spans() -> None:
    """Returns 0 when the trace has no spans."""
    assert _trace_start({"spans": []}) == 0


def test_trace_start_returns_zero_when_key_missing() -> None:
    """Returns 0 when trace has no 'spans' key."""
    assert _trace_start({}) == 0


# ---------------------------------------------------------------------------
# _load_session_from_api — HTTP error and connection error handling
# ---------------------------------------------------------------------------


def test_load_session_from_api_http_error_returns_none() -> None:
    """HTTP error (e.g. 404) returns None without raising."""
    request = httpx.Request("GET", "http://example.com")
    error_response = httpx.Response(404, request=request, text="Not found")

    with patch("httpx.get", side_effect=httpx.HTTPStatusError("404", request=request, response=error_response)):
        result = _load_session_from_api("http://example.com", "sess-1", "user-1")

    assert result is None


def test_load_session_from_api_connect_error_returns_none() -> None:
    """Connection error returns None without raising."""
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = _load_session_from_api("http://example.com", "sess-1", "user-1")

    assert result is None


def test_load_session_from_api_success_returns_dict() -> None:
    """Successful response returns the parsed JSON dict."""
    request = httpx.Request("GET", "http://example.com/api/sessions/sess-1/messages")
    response = httpx.Response(200, json={"id": "sess-1", "messages": []}, request=request)

    with patch("httpx.get", return_value=response):
        result = _load_session_from_api("http://example.com", "sess-1", "user-1")

    assert result is not None
    assert result["id"] == "sess-1"


# ---------------------------------------------------------------------------
# _load_audit_from_api — HTTP and connection error handling
# ---------------------------------------------------------------------------


def test_load_audit_from_api_http_error_returns_empty_list() -> None:
    """HTTP error returns empty list without raising."""
    request = httpx.Request("GET", "http://example.com")
    error_response = httpx.Response(403, request=request, text="Forbidden")

    with patch("httpx.get", side_effect=httpx.HTTPStatusError("403", request=request, response=error_response)):
        result = _load_audit_from_api("http://example.com", "sess-1", "user-1")

    assert result == []


def test_load_audit_from_api_connect_error_returns_empty_list() -> None:
    """Connection error returns empty list without raising."""
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        result = _load_audit_from_api("http://example.com", "sess-1", "user-1")

    assert result == []


# ---------------------------------------------------------------------------
# _format_header — branch without fhir_patient_id
# ---------------------------------------------------------------------------


def test_format_header_without_fhir_patient_id() -> None:
    """Session without fhir_patient_id omits the FHIR Patient ID line."""
    session = {
        "id": "sess-no-fhir",
        "openemr_user_id": "nurse-jane",
        "phase": "idle",
        "created_at": "2026-02-01T08:00:00",
        # No fhir_patient_id key
    }
    md = _format_header(session)
    assert "nurse-jane" in md
    assert "idle" in md
    assert "FHIR Patient ID" not in md


def test_format_header_fhir_patient_id_empty_string_omits_line() -> None:
    """fhir_patient_id='' is falsy, so the line should not appear."""
    session = {
        "id": "sess-empty",
        "openemr_user_id": "doc-a",
        "phase": "idle",
        "created_at": "2026-02-01T08:00:00",
        "fhir_patient_id": "",
    }
    md = _format_header(session)
    assert "FHIR Patient ID" not in md


def test_format_header_includes_session_id() -> None:
    """Session ID always appears in header output."""
    session = {
        "id": "my-session-xyz",
        "openemr_user_id": "doc-b",
        "phase": "reviewing",
        "created_at": "2026-02-01T08:00:00",
    }
    md = _format_header(session)
    assert "my-session-xyz" in md


# ---------------------------------------------------------------------------
# _format_page_context — minimal and partial contexts
# ---------------------------------------------------------------------------


def test_format_page_context_patient_id_only() -> None:
    """Context with only patient_id renders patient line, no encounter/page_type."""
    ctx = {"patient_id": "pid-42"}
    md = _format_page_context(ctx)
    assert "pid-42" in md
    assert "Encounter ID" not in md
    assert "Page Type" not in md


def test_format_page_context_with_page_type() -> None:
    """page_type field is rendered when present."""
    ctx = {"patient_id": "pid-5", "page_type": "encounter"}
    md = _format_page_context(ctx)
    assert "encounter" in md
    assert "Page Type" in md


def test_format_page_context_visible_data_with_none_value_skipped() -> None:
    """visible_data entries with value=None are not rendered."""
    ctx = {
        "patient_id": "pid-7",
        "visible_data": {"name": "John Doe", "dob": None},
    }
    md = _format_page_context(ctx)
    assert "John Doe" in md
    assert "dob" not in md


def test_format_page_context_non_dict_visible_data_ignored() -> None:
    """visible_data that is not a dict (e.g. list) is silently skipped."""
    ctx = {"patient_id": "pid-8", "visible_data": ["some", "list"]}
    md = _format_page_context(ctx)
    assert "pid-8" in md
    # No crash; list is not iterated as dict


# ---------------------------------------------------------------------------
# _format_manifest — empty items branch
# ---------------------------------------------------------------------------


def test_format_manifest_empty_items_shows_no_items_message() -> None:
    """Manifest with items=[] renders 'No manifest items.' instead of table."""
    manifest = {
        "id": "m-empty",
        "patient_id": "pid-1",
        "status": "draft",
        "items": [],
    }
    md = _format_manifest(manifest)
    assert "No manifest items." in md
    assert "| # |" not in md  # No table header


def test_format_manifest_execution_result_truncated_at_60() -> None:
    """execution_result longer than 60 chars is truncated with ellipsis."""
    long_result = "x" * 80
    manifest = {
        "id": "m-long",
        "patient_id": "pid-2",
        "status": "executed",
        "items": [
            {
                "id": "i-1",
                "action": "create",
                "resource_type": "Condition",
                "description": "Test",
                "status": "success",
                "confidence": "high",
                "execution_result": long_result,
            }
        ],
    }
    md = _format_manifest(manifest)
    assert "…" in md
    # truncated to 60 chars + ellipsis
    assert long_result not in md


def test_format_manifest_encounter_id_shown_when_present() -> None:
    """Manifest with encounter_id renders that field."""
    manifest = {
        "id": "m-enc",
        "patient_id": "pid-3",
        "encounter_id": "enc-99",
        "status": "draft",
        "items": [],
    }
    md = _format_manifest(manifest)
    assert "enc-99" in md
    assert "Encounter ID" in md


def test_format_manifest_no_encounter_id_omits_line() -> None:
    """Manifest without encounter_id does not show that field."""
    manifest = {
        "id": "m-noenc",
        "patient_id": "pid-4",
        "status": "draft",
        "items": [],
    }
    md = _format_manifest(manifest)
    assert "Encounter ID" not in md


# ---------------------------------------------------------------------------
# _format_jaeger_traces — empty list and no-spans branch
# ---------------------------------------------------------------------------


def test_format_jaeger_traces_empty_list() -> None:
    """Empty traces list renders '0 trace(s)' header with no trace sections."""
    md = _format_jaeger_traces([])
    assert "0" in md
    assert "trace(s)" in md
    assert "### Trace" not in md


def test_format_jaeger_traces_trace_with_no_spans_is_skipped() -> None:
    """A trace dict with spans=[] is skipped; no trace section rendered."""
    trace_no_spans = {
        "traceID": "deadbeef00000000",
        "spans": [],
    }
    md = _format_jaeger_traces([trace_no_spans])
    assert "**1**" in md  # bold count in header
    assert "trace(s)" in md
    assert "### Trace" not in md  # Skipped because no spans


def test_format_jaeger_traces_mixed_skips_empty_only() -> None:
    """Only traces with spans are rendered; empty-span trace is silently skipped."""
    trace_with_spans = {
        "traceID": "aaaa111100000000",
        "spans": [
            {
                "spanID": "sp-1",
                "operationName": "chat",
                "startTime": 1706000000000000,
                "duration": 1000000,
                "references": [],
                "tags": [],
                "logs": [],
            }
        ],
    }
    trace_empty = {"traceID": "bbbb222200000000", "spans": []}
    md = _format_jaeger_traces([trace_with_spans, trace_empty])
    assert "**2**" in md  # bold count in header
    assert "trace(s)" in md
    # The non-empty trace appears; the empty one is skipped
    assert "aaaa1111" in md
    # bbbb2222 trace section should NOT appear (it was skipped)
    assert "bbbb2222" not in md
