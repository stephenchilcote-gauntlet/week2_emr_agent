"""Review the developer feedback queue from the audit trail.

Queries audit events of type 'developer_feedback' (filed by the agent via
the send_developer_feedback tool) and 'message_feedback' (thumbs up/down
from clinicians).

Usage:
    uv run python scripts/feedback_queue.py                    # all feedback
    uv run python scripts/feedback_queue.py --category bug     # only bugs
    uv run python scripts/feedback_queue.py --type developer   # only agent-filed
    uv run python scripts/feedback_queue.py --type user        # only thumbs up/down
    uv run python scripts/feedback_queue.py --api-url http://localhost:8000  # local
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import httpx

_CATEGORY_ICONS = {
    "bug": "🐛",
    "feature_request": "💡",
    "usability": "🎨",
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Review developer feedback queue",
    )
    parser.add_argument(
        "--api-url",
        default="https://emragent.404.mn/agent-api",
        help="Agent API base URL (default: https://emragent.404.mn/agent-api)",
    )
    parser.add_argument(
        "--db-path",
        default="data/audit.db",
        help="Path to audit SQLite database for direct query (default: data/audit.db)",
    )
    parser.add_argument(
        "--category",
        choices=["bug", "feature_request", "usability"],
        help="Filter by feedback category",
    )
    parser.add_argument(
        "--type",
        choices=["developer", "user", "all"],
        default="all",
        dest="feedback_type",
        help="Filter: 'developer' (agent-filed), 'user' (thumbs), 'all' (default)",
    )
    parser.add_argument(
        "--user-id",
        default="1",
        help="OpenEMR user ID for API authentication (default: 1)",
    )
    args = parser.parse_args()

    events = _load_feedback_from_db(args.db_path, args.feedback_type, args.category)

    if not events:
        print("No feedback found.", file=sys.stderr)
        sys.exit(0)

    _render(events)


def _load_feedback_from_db(
    db_path: str,
    feedback_type: str,
    category: str | None,
) -> list[dict]:
    import sqlite3
    from pathlib import Path

    db = Path(db_path)
    if not db.exists():
        print(f"⚠ Database not found: {db_path}", file=sys.stderr)
        return []

    event_types = []
    if feedback_type in ("developer", "all"):
        event_types.append("developer_feedback")
    if feedback_type in ("user", "all"):
        event_types.append("message_feedback")

    placeholders = ",".join("?" for _ in event_types)
    query = (
        f"SELECT id, session_id, user_id, timestamp, event_type, summary, details "
        f"FROM audit_events WHERE event_type IN ({placeholders}) "
        f"ORDER BY timestamp DESC"
    )
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(query, event_types).fetchall()
    finally:
        conn.close()

    events = []
    for row in rows:
        details = json.loads(row[6])
        if category and details.get("category") != category:
            continue
        events.append({
            "id": row[0],
            "session_id": row[1],
            "user_id": row[2],
            "timestamp": row[3],
            "event_type": row[4],
            "summary": row[5],
            "details": details,
        })
    return events


def _render(events: list[dict]) -> None:
    print(f"# Feedback Queue — {len(events)} item(s)\n")

    for i, ev in enumerate(events, 1):
        etype = ev["event_type"]
        details = ev["details"]
        ts = ev["timestamp"]
        if "T" in ts:
            ts = ts.replace("T", " ")[:19]

        if etype == "developer_feedback":
            cat = details.get("category", "unknown")
            icon = _CATEGORY_ICONS.get(cat, "📝")
            msg = details.get("message", "")
            print(f"## {i}. {icon} [{cat}] — {ts}")
            print(f"   Session: {ev['session_id']}")
            print(f"   User: {ev['user_id']}")
            print(f"   {msg}")
        elif etype == "message_feedback":
            rating = details.get("rating", "?")
            icon = "👍" if rating == "up" else "👎"
            msg_idx = details.get("message_index", "?")
            print(f"## {i}. {icon} message #{msg_idx} — {ts}")
            print(f"   Session: {ev['session_id']}")
            print(f"   User: {ev['user_id']}")

        print()


if __name__ == "__main__":
    main()
