from __future__ import annotations

from src.observability.tracing import _sanitize_tool_args


def test_sanitize_tool_args_removes_non_identifier_content() -> None:
    payload = {
        "resource_type": "Condition",
        "description": "sensitive clinical narrative",
        "nested": {
            "patient_id": "pat-1",
            "note": "private",
        },
    }

    result = _sanitize_tool_args(payload)

    assert result["resource_type"] == "Condition"
    assert "description" not in result
    assert result["nested"]["patient_id"] == "pat-1"
    assert "note" not in result["nested"]
