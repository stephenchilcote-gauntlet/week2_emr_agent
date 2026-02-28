from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from scripts.session_trace import (
    _fetch_jaeger_traces,
    _format_conversation,
    _format_header,
    _format_jaeger_traces,
    _format_manifest,
    _format_page_context,
    _load_session,
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
