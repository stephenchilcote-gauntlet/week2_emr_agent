"""OpenTelemetry tracing setup for the OpenEMR clinical agent."""

from __future__ import annotations

import functools
import json
import os
import time
from typing import Any, Callable

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Tracer


class _NoopExporter(SpanExporter):
    def export(self, spans: Any) -> SpanExportResult:
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


ALLOWED_TRACE_KEYS = {
    "id",
    "item_id",
    "patient_id",
    "encounter_id",
    "resource_id",
    "resource_type",
    "ref",
    "source_reference",
    "target_resource_id",
    "endpoint",
    "method",
    "action",
}


def setup_tracing(service_name: str = "openemr-agent") -> Tracer:
    """Initialise the OTEL TracerProvider and return a Tracer.

    Attempts to export via OTLP/gRPC to the endpoint specified by
    ``OTEL_EXPORTER_OTLP_ENDPOINT``.  Falls back to ConsoleSpanExporter
    when the OTLP exporter is unavailable.
    """
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")

    exporter = None
    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
        except Exception:
            exporter = None

    if exporter is None:
        exporter = _NoopExporter()

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    return trace.get_tracer(service_name)


def trace_tool_call(tracer: Tracer) -> Callable:
    """Decorator that wraps a tool function with an OTEL span.

    Span attributes: ``tool.name``, ``tool.arguments``, ``tool.success``.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(f"tool.{func.__name__}") as span:
                span.set_attribute("tool.name", func.__name__)
                span.set_attribute(
                    "tool.arguments",
                    json.dumps(_sanitize_tool_args(kwargs), default=str),
                )
                try:
                    result = await func(*args, **kwargs)
                    span.set_attribute("tool.success", True)
                    return result
                except Exception as exc:
                    span.set_attribute("tool.success", False)
                    span.record_exception(exc)
                    raise

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(f"tool.{func.__name__}") as span:
                span.set_attribute("tool.name", func.__name__)
                span.set_attribute(
                    "tool.arguments",
                    json.dumps(_sanitize_tool_args(kwargs), default=str),
                )
                try:
                    result = func(*args, **kwargs)
                    span.set_attribute("tool.success", True)
                    return result
                except Exception as exc:
                    span.set_attribute("tool.success", False)
                    span.record_exception(exc)
                    raise

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def trace_llm_call(tracer: Tracer) -> Callable:
    """Decorator that wraps an LLM call with an OTEL span.

    Span attributes: ``llm.model``, ``llm.input_tokens``,
    ``llm.output_tokens``, ``llm.latency_ms``.

    The decorated function should return an object (or dict) with
    ``model``, ``input_tokens``, and ``output_tokens`` fields/keys.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(f"llm.{func.__name__}") as span:
                start = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                    latency_ms = (time.perf_counter() - start) * 1000
                    _set_llm_attributes(span, result, latency_ms)
                    return result
                except Exception as exc:
                    latency_ms = (time.perf_counter() - start) * 1000
                    span.set_attribute("llm.latency_ms", latency_ms)
                    span.record_exception(exc)
                    raise

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(f"llm.{func.__name__}") as span:
                start = time.perf_counter()
                try:
                    result = func(*args, **kwargs)
                    latency_ms = (time.perf_counter() - start) * 1000
                    _set_llm_attributes(span, result, latency_ms)
                    return result
                except Exception as exc:
                    latency_ms = (time.perf_counter() - start) * 1000
                    span.set_attribute("llm.latency_ms", latency_ms)
                    span.record_exception(exc)
                    raise

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def trace_verification(tracer: Tracer) -> Callable:
    """Decorator that wraps a verification function with an OTEL span.

    Span attributes: ``verification.check_name``, ``verification.passed``,
    ``verification.item_count``.

    The decorated function should return a result with ``passed`` and
    optionally ``results`` (list) attributes.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(
                f"verification.{func.__name__}"
            ) as span:
                span.set_attribute("verification.check_name", func.__name__)
                try:
                    result = await func(*args, **kwargs)
                    _set_verification_attributes(span, result)
                    return result
                except Exception as exc:
                    span.set_attribute("verification.passed", False)
                    span.record_exception(exc)
                    raise

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(
                f"verification.{func.__name__}"
            ) as span:
                span.set_attribute("verification.check_name", func.__name__)
                try:
                    result = func(*args, **kwargs)
                    _set_verification_attributes(span, result)
                    return result
                except Exception as exc:
                    span.set_attribute("verification.passed", False)
                    span.record_exception(exc)
                    raise

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def _set_llm_attributes(span: Any, result: Any, latency_ms: float) -> None:
    """Extract LLM metadata from the result and set span attributes."""
    span.set_attribute("llm.latency_ms", latency_ms)

    if isinstance(result, dict):
        span.set_attribute("llm.model", result.get("model", "unknown"))
        span.set_attribute("llm.input_tokens", result.get("input_tokens", 0))
        span.set_attribute("llm.output_tokens", result.get("output_tokens", 0))
    elif hasattr(result, "model"):
        span.set_attribute("llm.model", getattr(result, "model", "unknown"))
        usage = getattr(result, "usage", None)
        if usage:
            span.set_attribute(
                "llm.input_tokens", getattr(usage, "input_tokens", 0)
            )
            span.set_attribute(
                "llm.output_tokens", getattr(usage, "output_tokens", 0)
            )


def _set_verification_attributes(span: Any, result: Any) -> None:
    """Extract verification metadata from the result and set span attributes."""
    if hasattr(result, "passed"):
        span.set_attribute("verification.passed", result.passed)
    elif isinstance(result, dict):
        span.set_attribute("verification.passed", result.get("passed", False))

    if hasattr(result, "results"):
        span.set_attribute("verification.item_count", len(result.results))
    elif isinstance(result, list):
        span.set_attribute("verification.item_count", len(result))


def _sanitize_tool_args(value: Any) -> Any:
    """Only keep identifier-like keys to avoid emitting PHI content."""
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if key in ALLOWED_TRACE_KEYS:
                sanitized[key] = _sanitize_tool_args(item)
            elif isinstance(item, (dict, list)):
                nested = _sanitize_tool_args(item)
                if nested:
                    sanitized[key] = nested
        return sanitized
    if isinstance(value, list):
        return [_sanitize_tool_args(item) for item in value]
    return value
