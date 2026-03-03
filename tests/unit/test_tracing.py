from __future__ import annotations

import asyncio

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.trace import Tracer

from src.observability.tracing import (
    ALLOWED_TRACE_KEYS,
    _NoopExporter,
    _sanitize_tool_args,
    _set_llm_attributes,
    _set_verification_attributes,
    setup_tracing,
    trace_llm_call,
    trace_tool_call,
    trace_verification,
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


# ---------------------------------------------------------------------------
# _NoopExporter
# ---------------------------------------------------------------------------


def test_noop_exporter_returns_success() -> None:
    """_NoopExporter.export always returns SUCCESS."""
    exporter = _NoopExporter()
    result = exporter.export([])
    assert result == SpanExportResult.SUCCESS


def test_noop_exporter_shutdown_is_noop() -> None:
    """_NoopExporter.shutdown does not raise."""
    exporter = _NoopExporter()
    exporter.shutdown()  # should not raise


# ---------------------------------------------------------------------------
# ALLOWED_TRACE_KEYS
# ---------------------------------------------------------------------------


def test_allowed_trace_keys_contains_expected_fields() -> None:
    assert "patient_id" in ALLOWED_TRACE_KEYS
    assert "resource_type" in ALLOWED_TRACE_KEYS
    assert "encounter_id" in ALLOWED_TRACE_KEYS
    assert "method" in ALLOWED_TRACE_KEYS
    assert "action" in ALLOWED_TRACE_KEYS


# ---------------------------------------------------------------------------
# _set_llm_attributes — extra edge cases
# ---------------------------------------------------------------------------


def test_set_llm_attributes_with_object_no_usage() -> None:
    """Object with model but no usage attribute → only latency and model set."""
    class LLMResult:
        model = "claude-3"
        # no usage attribute

    span = MockSpan()
    _set_llm_attributes(span, LLMResult(), latency_ms=10.0)

    assert span.attributes["llm.model"] == "claude-3"
    assert span.attributes["llm.latency_ms"] == 10.0
    # usage branch not triggered → input/output_tokens not set
    assert "llm.input_tokens" not in span.attributes


def test_set_llm_attributes_non_dict_non_object() -> None:
    """A plain string result → only latency_ms is set."""
    span = MockSpan()
    _set_llm_attributes(span, "not-a-result", latency_ms=5.0)

    assert span.attributes["llm.latency_ms"] == 5.0
    assert "llm.model" not in span.attributes


# ---------------------------------------------------------------------------
# _set_verification_attributes — extra edge cases
# ---------------------------------------------------------------------------


def test_set_verification_attributes_with_passed_false_and_results() -> None:
    class VerResult:
        passed = False
        results = ["err-1"]

    span = MockSpan()
    _set_verification_attributes(span, VerResult())

    assert span.attributes["verification.passed"] is False
    assert span.attributes["verification.item_count"] == 1


def test_set_verification_attributes_dict_with_passed_true() -> None:
    span = MockSpan()
    _set_verification_attributes(span, {"passed": True})

    assert span.attributes["verification.passed"] is True


# ---------------------------------------------------------------------------
# trace_tool_call decorator — async
# ---------------------------------------------------------------------------


def test_trace_tool_call_async_success_returns_result() -> None:
    """trace_tool_call wraps an async function and returns its result."""
    tracer = setup_tracing("unit-test-tool")

    @trace_tool_call(tracer)
    async def my_tool(x: int) -> dict:
        return {"value": x * 2}

    result = asyncio.run(my_tool(x=5))
    assert result == {"value": 10}


def test_trace_tool_call_async_propagates_exception() -> None:
    """trace_tool_call re-raises exceptions from the wrapped async function."""
    tracer = setup_tracing("unit-test-tool")

    @trace_tool_call(tracer)
    async def failing_tool() -> dict:
        raise ValueError("tool error")

    with pytest.raises(ValueError, match="tool error"):
        asyncio.run(failing_tool())


# ---------------------------------------------------------------------------
# trace_tool_call decorator — sync
# ---------------------------------------------------------------------------


def test_trace_tool_call_sync_success_returns_result() -> None:
    """trace_tool_call wraps a sync function and returns its result."""
    tracer = setup_tracing("unit-test-sync")

    @trace_tool_call(tracer)
    def my_sync_tool(x: int) -> dict:
        return {"doubled": x * 2}

    result = my_sync_tool(x=7)
    assert result == {"doubled": 14}


def test_trace_tool_call_sync_propagates_exception() -> None:
    """trace_tool_call re-raises exceptions from the wrapped sync function."""
    tracer = setup_tracing("unit-test-sync")

    @trace_tool_call(tracer)
    def sync_fail() -> None:
        raise RuntimeError("sync error")

    with pytest.raises(RuntimeError, match="sync error"):
        sync_fail()


# ---------------------------------------------------------------------------
# trace_llm_call decorator
# ---------------------------------------------------------------------------


def test_trace_llm_call_async_success() -> None:
    """trace_llm_call wraps an async LLM call and returns its result."""
    tracer = setup_tracing("unit-test-llm")

    @trace_llm_call(tracer)
    async def call_llm() -> dict:
        return {"model": "claude-3", "input_tokens": 100, "output_tokens": 50}

    result = asyncio.run(call_llm())
    assert result["model"] == "claude-3"
    assert result["input_tokens"] == 100


def test_trace_llm_call_async_propagates_exception() -> None:
    """trace_llm_call re-raises exceptions from the LLM call."""
    tracer = setup_tracing("unit-test-llm")

    @trace_llm_call(tracer)
    async def failing_llm() -> dict:
        raise ConnectionError("LLM offline")

    with pytest.raises(ConnectionError, match="LLM offline"):
        asyncio.run(failing_llm())


def test_trace_llm_call_sync_success() -> None:
    """trace_llm_call also works with sync functions."""
    tracer = setup_tracing("unit-test-llm-sync")

    @trace_llm_call(tracer)
    def sync_llm() -> dict:
        return {"model": "gpt-4", "input_tokens": 50, "output_tokens": 25}

    result = sync_llm()
    assert result["model"] == "gpt-4"


# ---------------------------------------------------------------------------
# trace_verification decorator
# ---------------------------------------------------------------------------


def test_trace_verification_async_success() -> None:
    """trace_verification wraps an async verification function."""
    tracer = setup_tracing("unit-test-verif")

    class CheckResult:
        passed = True
        results = ["ok"]

    @trace_verification(tracer)
    async def my_check() -> CheckResult:
        return CheckResult()

    result = asyncio.run(my_check())
    assert result.passed is True


def test_trace_verification_async_propagates_exception() -> None:
    """trace_verification re-raises exceptions from async verification."""
    tracer = setup_tracing("unit-test-verif")

    @trace_verification(tracer)
    async def failing_check() -> None:
        raise ValueError("check failed")

    with pytest.raises(ValueError, match="check failed"):
        asyncio.run(failing_check())


def test_trace_verification_sync_success() -> None:
    """trace_verification wraps a sync verification function."""
    tracer = setup_tracing("unit-test-verif-sync")

    class CheckResult:
        passed = False
        results: list = []

    @trace_verification(tracer)
    def sync_check() -> CheckResult:
        return CheckResult()

    result = sync_check()
    assert result.passed is False
