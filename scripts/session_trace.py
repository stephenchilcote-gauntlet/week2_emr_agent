"""Retrieve full session trace by UUID from prod VPS.

One-step audit: pass a session UUID from the sidebar UI, get the complete
conversation record with tool calls, manifest, and Jaeger trace timeline.
Defaults to fetching from production VPS (https://emragent.404.mn).

Usage:
    uv run python scripts/session_trace.py <session-uuid>
    uv run python scripts/session_trace.py <session-uuid> --api-url http://localhost:8000  # local
    uv run python scripts/session_trace.py <session-uuid> --jaeger-url http://localhost:16686
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

MAX_CONTENT_CHARS = 500


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Retrieve full session trace by UUID",
    )
    parser.add_argument("session_id", help="Session UUID from the sidebar UI")
    parser.add_argument(
        "--db-path",
        default="data/sessions.db",
        help="Path to sessions SQLite database (default: data/sessions.db)",
    )
    parser.add_argument(
        "--jaeger-url",
        default="http://localhost:16686",
        help="Jaeger query base URL (default: http://localhost:16686)",
    )
    parser.add_argument(
        "--api-url",
        default="https://emragent.404.mn/agent-api",
        help="Remote agent API base URL (default: https://emragent.404.mn/agent-api). "
             "When set, fetches session data from the API instead of local SQLite.",
    )
    parser.add_argument(
        "--user-id",
        default="1",
        help="OpenEMR user ID for API authentication (default: 1)",
    )
    args = parser.parse_args()

    session = _load_session_from_api(args.api_url, args.session_id, args.user_id)
    audit_events = _load_audit_from_api(args.api_url, args.session_id, args.user_id)
    traces = _fetch_jaeger_traces(args.jaeger_url, args.session_id)

    if session is None and not traces and not audit_events:
        print(f"No data found for session {args.session_id}", file=sys.stderr)
        sys.exit(1)

    parts: list[str] = []
    parts.append(f"# Session Trace: `{args.session_id}`\n")

    if session:
        parts.append(_format_header(session))
        ctx = _format_page_context(session.get("page_context"))
        if ctx:
            parts.append(ctx)
        parts.append(_format_conversation(session.get("messages", [])))
        manifest = _format_manifest(session.get("manifest"))
        if manifest:
            parts.append(manifest)
    else:
        parts.append(
            "> ⚠️ Session not found in database. Showing Jaeger traces only.\n"
        )

    if audit_events:
        parts.append(_format_audit_events(audit_events))

    if traces:
        parts.append(_format_jaeger_traces(traces))
    else:
        parts.append("\n## Jaeger Traces\n\nNo traces found in Jaeger.\n")

    print("\n".join(parts))


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _load_session_from_api(api_url: str, session_id: str, user_id: str) -> dict | None:
    url = f"{api_url.rstrip('/')}/api/sessions/{session_id}/messages"
    try:
        resp = httpx.get(url, headers={"openemr_user_id": user_id}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        print(f"⚠ API error {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        return None
    except httpx.ConnectError as exc:
        print(f"⚠ Cannot connect to API at {api_url}: {exc}", file=sys.stderr)
        return None


def _load_audit_from_api(api_url: str, session_id: str, user_id: str) -> list[dict]:
    url = f"{api_url.rstrip('/')}/api/sessions/{session_id}/audit"
    try:
        resp = httpx.get(url, headers={"openemr_user_id": user_id}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        print(f"⚠ Audit API error {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        return []
    except httpx.ConnectError as exc:
        print(f"⚠ Cannot connect to API for audit at {api_url}: {exc}", file=sys.stderr)
        return []


def _load_session(db_path: str, session_id: str) -> dict | None:
    db = Path(db_path)
    if not db.exists():
        print(f"⚠ Database not found: {db_path}", file=sys.stderr)
        return None
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT payload FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return json.loads(row[0])


def _fetch_jaeger_traces(jaeger_url: str, session_id: str) -> list[dict]:
    now_us = int(datetime.now(timezone.utc).timestamp() * 1_000_000)
    thirty_days_us = 30 * 24 * 60 * 60 * 1_000_000
    params = {
        "service": "openemr-agent",
        "tags": json.dumps({"session.id": session_id}),
        "limit": "50",
        "start": str(now_us - thirty_days_us),
        "end": str(now_us),
        "lookback": "720h",
    }
    url = f"{jaeger_url}/api/traces"
    try:
        resp = httpx.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json().get("data", [])
    except httpx.ConnectError:
        print("⚠ Jaeger not reachable", file=sys.stderr)
        return []
    except Exception as exc:
        print(f"⚠ Jaeger query failed: {exc}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# Markdown formatting — session data
# ---------------------------------------------------------------------------


def _format_header(session: dict) -> str:
    lines = [
        "## Session Info\n",
        f"- **ID:** `{session.get('id', 'unknown')}`",
        f"- **User:** `{session.get('openemr_user_id', 'unknown')}`",
        f"- **Phase:** {session.get('phase', 'unknown')}",
        f"- **Created:** {session.get('created_at', 'unknown')}",
    ]
    if session.get("fhir_patient_id"):
        lines.append(f"- **FHIR Patient ID:** `{session['fhir_patient_id']}`")
    lines.append("")
    return "\n".join(lines)


def _format_page_context(ctx: dict | None) -> str:
    if not ctx:
        return ""
    lines = ["## Page Context\n"]
    if ctx.get("patient_id"):
        lines.append(f"- **Patient ID:** `{ctx['patient_id']}`")
    if ctx.get("encounter_id"):
        lines.append(f"- **Encounter ID:** `{ctx['encounter_id']}`")
    if ctx.get("page_type"):
        lines.append(f"- **Page Type:** {ctx['page_type']}")
    visible = ctx.get("visible_data")
    if visible and isinstance(visible, dict):
        for key, val in visible.items():
            if val is not None:
                lines.append(f"- **{key}:** {val}")
    lines.append("")
    return "\n".join(lines)


def _format_conversation(messages: list[dict]) -> str:
    if not messages:
        return "## Conversation\n\nNo messages.\n"

    lines = ["## Conversation\n"]

    # Map tool_call id → name for cross-referencing with results
    tc_name_map: dict[str, str] = {}
    for msg in messages:
        for tc in msg.get("tool_calls") or []:
            tc_name_map[tc["id"]] = tc["name"]

    for i, msg in enumerate(messages, 1):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")

        if role == "user":
            lines.append(f"### Message {i} — 👤 User\n")
            lines.append(content)
            lines.append("")

        elif role == "assistant":
            lines.append(f"### Message {i} — 🤖 Assistant\n")
            if content:
                lines.append(content)
                lines.append("")
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                lines.append("**Tool calls:**\n")
                for j, tc in enumerate(tool_calls, 1):
                    args_str = json.dumps(
                        tc.get("arguments", {}), default=str
                    )
                    if len(args_str) > MAX_CONTENT_CHARS:
                        args_str = args_str[:MAX_CONTENT_CHARS] + "…"
                    lines.append(f"{j}. `{tc['name']}` — `{args_str}`")
                lines.append("")

        elif role == "tool":
            tool_results = msg.get("tool_results")
            if tool_results:
                lines.append(f"### Message {i} — 🔧 Tool Results\n")
                for tr in tool_results:
                    name = tc_name_map.get(
                        tr.get("tool_call_id", ""), tr.get("tool_call_id", "?")[:8]
                    )
                    status = "❌" if tr.get("is_error") else "✅"
                    lines.append(f"**{name}** {status}\n")
                    result_content = tr.get("content", "")
                    if len(result_content) > MAX_CONTENT_CHARS:
                        result_content = (
                            result_content[:MAX_CONTENT_CHARS] + "\n… (truncated)"
                        )
                    lines.append(f"```\n{result_content}\n```\n")

    return "\n".join(lines)


def _format_manifest(manifest: dict | None) -> str:
    if not manifest:
        return ""

    lines = [
        "## Manifest\n",
        f"- **Manifest ID:** `{manifest.get('id', 'unknown')}`",
        f"- **Patient ID:** `{manifest.get('patient_id', 'unknown')}`",
    ]
    if manifest.get("encounter_id"):
        lines.append(f"- **Encounter ID:** `{manifest['encounter_id']}`")
    lines.append(f"- **Status:** {manifest.get('status', 'unknown')}")
    lines.append("")

    items = manifest.get("items", [])
    if items:
        lines.append(
            "| # | Action | Resource | Description | Status | Confidence | Result |"
        )
        lines.append(
            "|---|--------|----------|-------------|--------|------------|--------|"
        )
        for idx, item in enumerate(items, 1):
            result = (item.get("execution_result") or "")[:60]
            if len(item.get("execution_result") or "") > 60:
                result += "…"
            lines.append(
                f"| {idx} "
                f"| {item.get('action', '')} "
                f"| {item.get('resource_type', '')} "
                f"| {item.get('description', '')} "
                f"| {item.get('status', '')} "
                f"| {item.get('confidence', '')} "
                f"| {result} |"
            )
    else:
        lines.append("No manifest items.")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown formatting — audit events
# ---------------------------------------------------------------------------

_AUDIT_ICONS = {
    "chat_received": "💬",
    "manifest_reviewed": "📋",
    "manifest_executed": "🚀",
}


def _format_audit_events(events: list[dict]) -> str:
    lines = [f"\n## Audit Trail\n\nFound **{len(events)}** event(s).\n"]
    lines.append("| # | Time | Type | Summary | Details |")
    lines.append("|---|------|------|---------|---------|")
    for i, ev in enumerate(events, 1):
        ts = ev.get("timestamp", "")
        if "T" in ts:
            ts = ts.split("T")[1][:8]  # HH:MM:SS
        etype_key = ev.get("event_type", "")
        if etype_key == "message_feedback":
            icon = "👍" if ev.get("details", {}).get("rating") == "up" else "👎"
        else:
            icon = _AUDIT_ICONS.get(etype_key, "📝")
        etype = etype_key
        summary = ev.get("summary", "")
        details = ev.get("details", {})
        detail_str = ", ".join(f"{k}={v}" for k, v in details.items()) if details else ""
        if len(detail_str) > 80:
            detail_str = detail_str[:80] + "…"
        lines.append(f"| {i} | {ts} | {icon} {etype} | {summary} | {detail_str} |")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown formatting — Jaeger traces
# ---------------------------------------------------------------------------


def _format_jaeger_traces(traces: list[dict]) -> str:
    lines = [f"\n## Jaeger Traces\n\nFound **{len(traces)}** trace(s).\n"]

    for t_idx, trace_data in enumerate(
        sorted(traces, key=lambda t: _trace_start(t)), 1
    ):
        trace_id = trace_data.get("traceID", "unknown")
        spans = trace_data.get("spans", [])
        if not spans:
            continue

        spans.sort(key=lambda s: s.get("startTime", 0))

        # Build parent→children map
        span_ids_in_trace = {s["spanID"] for s in spans}
        span_map: dict[str, dict] = {}
        children: dict[str, list[str]] = {}
        root_ids: list[str] = []

        for span in spans:
            sid = span["spanID"]
            span_map[sid] = span
            parent = _parent_span_id(span)
            if parent and parent in span_ids_in_trace:
                children.setdefault(parent, []).append(sid)
            else:
                root_ids.append(sid)

        # Sort children by start time
        for kids in children.values():
            kids.sort(key=lambda sid: span_map[sid].get("startTime", 0))

        trace_start_us = spans[0]["startTime"]
        trace_end_us = max(s["startTime"] + s.get("duration", 0) for s in spans)
        duration_ms = (trace_end_us - trace_start_us) / 1000
        start_dt = datetime.fromtimestamp(trace_start_us / 1_000_000, tz=timezone.utc)

        lines.append(f"### Trace {t_idx} — `{trace_id[:16]}…`\n")
        lines.append(
            f"**Started:** {start_dt.strftime('%Y-%m-%d %H:%M:%S UTC')} "
            f"| **Duration:** {duration_ms:.0f}ms "
            f"| **Spans:** {len(spans)}\n"
        )

        # Render span tree via DFS
        def render(sid: str, depth: int) -> None:
            span = span_map[sid]
            op = span.get("operationName", "?")
            dur = span.get("duration", 0) / 1000
            attrs = _extract_key_tags(span)
            indent = "  " * depth
            prefix = "└ " if depth > 0 else ""
            attr_str = f" — {', '.join(attrs)}" if attrs else ""
            lines.append(f"{indent}{prefix}**{op}** ({dur:.0f}ms){attr_str}")
            for child_id in children.get(sid, []):
                render(child_id, depth + 1)

        for rid in root_ids:
            render(rid, 0)

        lines.append("")

    return "\n".join(lines)


_KEY_TAGS = {
    "session.id",
    "http.status_code",
    "http.method",
    "llm.model",
    "llm.input_tokens",
    "llm.output_tokens",
    "llm.latency_ms",
    "tool.name",
    "tool.success",
    "verification.passed",
    "verification.item_count",
}


def _extract_key_tags(span: dict) -> list[str]:
    attrs: list[str] = []
    for tag in span.get("tags", []):
        key = tag.get("key", "")
        if key not in _KEY_TAGS:
            continue
        val = tag.get("value", "")
        if key == "llm.latency_ms" and isinstance(val, (int, float)):
            attrs.append(f"{key}={val:.0f}ms")
        else:
            attrs.append(f"{key}={val}")
    return attrs


def _parent_span_id(span: dict) -> str | None:
    for ref in span.get("references", []):
        if ref.get("refType") == "CHILD_OF":
            return ref.get("spanID")
    return None


def _trace_start(trace: dict) -> int:
    spans = trace.get("spans", [])
    if not spans:
        return 0
    return min(s.get("startTime", 0) for s in spans)


if __name__ == "__main__":
    main()
