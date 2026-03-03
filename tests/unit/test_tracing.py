from __future__ import annotations

from opentelemetry.trace import Tracer

from src.observability.tracing import (
    _sanitize_tool_args,
    _set_llm_attributes,
    _set_verification_attributes,
    setup_tracing,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockSpan:
    """Minimal span stub that records set_attribute calls."""

    def __init__(self) -> None:
        self.attributes: dict[str, object] = {}

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value


# ---------------------------------------------------------------------------
# _sanitize_tool_args
# ---------------------------------------------------------------------------

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


def test_sanitize_tool_args_empty_dict() -> None:
    result = _sanitize_tool_args({})
    assert result == {}


def test_sanitize_tool_args_list_input() -> None:
    # A list of dicts — only allowed keys survive inside each element.
    items = [
        {"patient_id": "p-1", "phi": "secret"},
        {"resource_type": "Allergy", "notes": "more secrets"},
    ]
    result = _sanitize_tool_args(items)

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0] == {"patient_id": "p-1"}
    assert result[1] == {"resource_type": "Allergy"}


def test_sanitize_tool_args_non_dict_primitive() -> None:
    # Primitive values (str, int, None) are returned unchanged.
    assert _sanitize_tool_args("hello") == "hello"
    assert _sanitize_tool_args(42) == 42
    assert _sanitize_tool_args(None) is None


def test_sanitize_tool_args_nested_list() -> None:
    # A list nested under a non-allowed key — the list is processed recursively;
    # if it produces non-empty output it is kept under that key.
    payload = {
        "items": [
            {"patient_id": "p-99", "phi": "drop me"},
        ],
    }
    result = _sanitize_tool_args(payload)

    # "items" is not in ALLOWED_TRACE_KEYS so it goes through the nested-list path.
    # The nested list produces [{"patient_id": "p-99"}] which is truthy → kept.
    assert "items" in result
    assert result["items"] == [{"patient_id": "p-99"}]


def test_sanitize_tool_args_all_keys_removed() -> None:
    # A dict whose only keys hold scalar PHI and are not in ALLOWED_TRACE_KEYS.
    payload = {"note": "private text", "description": "also private"}
    result = _sanitize_tool_args(payload)
    assert result == {}


# ---------------------------------------------------------------------------
# _set_llm_attributes
# ---------------------------------------------------------------------------

def test_set_llm_attributes_with_dict_result() -> None:
    span = MockSpan()
    result = {"model": "claude-3", "input_tokens": 100, "output_tokens": 50}
    _set_llm_attributes(span, result, latency_ms=123.4)

    assert span.attributes["llm.latency_ms"] == 123.4
    assert span.attributes["llm.model"] == "claude-3"
    assert span.attributes["llm.input_tokens"] == 100
    assert span.attributes["llm.output_tokens"] == 50


def test_set_llm_attributes_with_object_result() -> None:
    class Usage:
        input_tokens = 200
        output_tokens = 75

    class LLMResult:
        model = "claude-opus"
        usage = Usage()

    span = MockSpan()
    _set_llm_attributes(span, LLMResult(), latency_ms=55.0)

    assert span.attributes["llm.latency_ms"] == 55.0
    assert span.attributes["llm.model"] == "claude-opus"
    assert span.attributes["llm.input_tokens"] == 200
    assert span.attributes["llm.output_tokens"] == 75


def test_set_llm_attributes_missing_keys() -> None:
    # A partial dict — missing keys should fall back to defaults (0 / "unknown").
    span = MockSpan()
    _set_llm_attributes(span, {"model": "gpt-4"}, latency_ms=10.0)

    assert span.attributes["llm.latency_ms"] == 10.0
    assert span.attributes["llm.model"] == "gpt-4"
    assert span.attributes["llm.input_tokens"] == 0
    assert span.attributes["llm.output_tokens"] == 0


# ---------------------------------------------------------------------------
# _set_verification_attributes
# ---------------------------------------------------------------------------

def test_set_verification_attributes_with_passed_object() -> None:
    class VerificationResult:
        passed = True
        results = ["ok-1", "ok-2", "ok-3"]

    span = MockSpan()
    _set_verification_attributes(span, VerificationResult())

    assert span.attributes["verification.passed"] is True
    assert span.attributes["verification.item_count"] == 3


def test_set_verification_attributes_with_dict_result() -> None:
    span = MockSpan()
    _set_verification_attributes(span, {"passed": False})

    assert span.attributes["verification.passed"] is False
    # No "results" key and not a list → item_count not set.
    assert "verification.item_count" not in span.attributes


def test_set_verification_attributes_with_list() -> None:
    span = MockSpan()
    items = ["check-a", "check-b"]
    _set_verification_attributes(span, items)

    # A plain list has no "passed" attribute and is not a dict → passed not set.
    assert "verification.passed" not in span.attributes
    assert span.attributes["verification.item_count"] == 2


# ---------------------------------------------------------------------------
# setup_tracing
# ---------------------------------------------------------------------------

def test_setup_tracing_returns_tracer() -> None:
    # No OTLP endpoint configured → falls back to _NoopExporter; must still
    # return an object that exposes start_as_current_span.
    tracer = setup_tracing(service_name="test-service")
    assert hasattr(tracer, "start_as_current_span")
