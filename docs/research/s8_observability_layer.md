# §8 — The Observability Layer: Research Notes

> Research for prose author. Not reader-facing.

---

## 1. Architecture Overview

The observability layer lives in a single module:

```
src/observability/
├── __init__.py          # empty — package marker only
└── tracing.py           # all OTEL setup + 3 decorators + 2 attribute helpers
```

It is consumed in exactly one place — `src/api/main.py` — at module level (line 60):

```python
tracer = setup_tracing("openemr-agent")
```

The stack: **application code → OTEL SDK → BatchSpanProcessor → OTLP/gRPC exporter → Jaeger**.

---

## 2. Dependencies (pyproject.toml lines 11–15)

```toml
"opentelemetry-api>=1.39.1",
"opentelemetry-exporter-otlp>=1.39.1",
"opentelemetry-instrumentation-fastapi>=0.60b1",
"opentelemetry-instrumentation-httpx>=0.60b1",
"opentelemetry-sdk>=1.39.1",
```

Five packages. Note the **beta** version pins on the instrumentation packages (`0.60b1`); the OTEL instrumentation ecosystem trails the core SDK.

---

## 3. `setup_tracing()` — Provider Bootstrap

**File:** `src/observability/tracing.py:18-47`

```python
def setup_tracing(service_name: str = "openemr-agent") -> Tracer:
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
        exporter = ConsoleSpanExporter()

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)
```

### Key design decisions

| Decision | Detail |
|---|---|
| **Lazy OTLP import** | `OTLPSpanExporter` is imported inside the `try` block, so the gRPC dependency is optional at import time. If it fails (missing dep, wrong protobuf version), falls back silently to `ConsoleSpanExporter`. |
| **Console fallback** | When `OTEL_EXPORTER_OTLP_ENDPOINT` is unset OR the OTLP import fails, spans print to stdout. This means **local dev always gets traces** without Jaeger. |
| **BatchSpanProcessor** | Spans are batched before export (async, non-blocking). The default batch size/interval from the SDK applies. |
| **Global provider** | `trace.set_tracer_provider(provider)` sets the process-global provider — any auto-instrumentation library (FastAPI, HTTPX) will automatically use it. |

### Edge case / gotcha
The function is called at **module import time** (line 60 of `main.py`), which means:
- The env var `OTEL_EXPORTER_OTLP_ENDPOINT` must be set **before** the module is imported.
- If the OTLP exporter constructor raises (e.g. unreachable Jaeger endpoint), it catches `Exception` broadly and falls back — no crash, but **no warning logged either**. Silent fallback.

---

## 4. The Three Decorators

All three follow the same structural pattern: **decorator factory** that takes a `Tracer`, returns a decorator, which wraps the function in both async and sync variants.

### 4a. `trace_tool_call(tracer)` — lines 50–91

```python
def trace_tool_call(tracer: Tracer) -> Callable:
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            with tracer.start_as_current_span(f"tool.{func.__name__}") as span:
                span.set_attribute("tool.name", func.__name__)
                span.set_attribute("tool.arguments", json.dumps(kwargs, default=str))
                try:
                    result = await func(*args, **kwargs)
                    span.set_attribute("tool.success", True)
                    return result
                except Exception as exc:
                    span.set_attribute("tool.success", False)
                    span.record_exception(exc)
                    raise
        # ... sync_wrapper identical structure ...
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    return decorator
```

**Span attributes:**
| Attribute | Type | Source |
|---|---|---|
| `tool.name` | string | `func.__name__` |
| `tool.arguments` | string (JSON) | `json.dumps(kwargs, default=str)` |
| `tool.success` | bool | set after execution |

**Notable:** `tool.arguments` serialises `kwargs` only — positional `args` are not captured. The `default=str` fallback prevents serialization crashes on non-JSON types.

**Exception handling:** `span.record_exception(exc)` attaches the full exception to the span (including traceback), then re-raises. The span gets both `tool.success=False` AND the exception event.

### 4b. `trace_llm_call(tracer)` — lines 94–141

```python
def trace_llm_call(tracer: Tracer) -> Callable:
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
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
```

**Span attributes:**
| Attribute | Type | Source |
|---|---|---|
| `llm.latency_ms` | float | `time.perf_counter()` delta × 1000 |
| `llm.model` | string | `result.model` or `result["model"]` |
| `llm.input_tokens` | int | `result.usage.input_tokens` or `result["input_tokens"]` |
| `llm.output_tokens` | int | `result.usage.output_tokens` or `result["output_tokens"]` |

**Notable:** Uses `time.perf_counter()` not `time.time()` — monotonic, not affected by clock drift. The latency is **inclusive** of network I/O, deserialization, etc.

**Dual extraction in `_set_llm_attributes()` (lines 194–211):** handles both dict-style and object-style (Anthropic SDK) responses. Dict path: `result.get("model")`. Object path: `getattr(result, "model")` + `getattr(result, "usage")` sub-object. This duck-typing means it works with raw Anthropic `Message` objects AND any dict-based mock/test response.

**Edge case on error:** When the LLM call raises, only `llm.latency_ms` is set — no model/token attributes. This means error spans are distinguishable in Jaeger by the **absence** of token count attributes.

### 4c. `trace_verification(tracer)` — lines 144–191

```python
def trace_verification(tracer: Tracer) -> Callable:
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            with tracer.start_as_current_span(f"verification.{func.__name__}") as span:
                span.set_attribute("verification.check_name", func.__name__)
                try:
                    result = await func(*args, **kwargs)
                    _set_verification_attributes(span, result)
                    return result
                except Exception as exc:
                    span.set_attribute("verification.passed", False)
                    span.record_exception(exc)
                    raise
```

**Span attributes:**
| Attribute | Type | Source |
|---|---|---|
| `verification.check_name` | string | `func.__name__` |
| `verification.passed` | bool | `result.passed` or `result["passed"]` |
| `verification.item_count` | int | `len(result.results)` or `len(result)` |

**`_set_verification_attributes()` (lines 214–224):** Another duck-typed extractor. Handles object with `.passed` + `.results` attributes OR dict with `"passed"` key OR plain list (sets `item_count` from list length).

**Edge case:** If `result` is a plain list (no `.passed`), `verification.passed` is never set — the attribute is simply absent from the span. Only `verification.item_count` would appear.

---

## 5. Shared Decorator Architecture — Pattern Worth Noting

All three decorators share an identical meta-structure:

```
decorator_factory(tracer) → decorator(func) → async_wrapper / sync_wrapper
```

The **async/sync detection** happens via `asyncio.iscoroutinefunction(func)` at decoration time (not at call time). This is a one-time check — correct for functions but **would fail for callable objects** with both `__call__` and `__await__`.

The `import asyncio` is inside the decorator body (not at module top). This is unusual but harmless — likely done to keep the import lazy, though `asyncio` is a stdlib module.

**Suggested example for text:** Show `trace_tool_call` as the primary example (simplest), then describe `trace_llm_call` and `trace_verification` as variations that add domain-specific attribute extraction.

---

## 6. Auto-instrumentation: FastAPI

**File:** `src/api/main.py:11, 72`

```python
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
# ...
app = FastAPI(title="OpenEMR Clinical Agent", version="0.1.0", lifespan=lifespan)
# ...
FastAPIInstrumentor.instrument_app(app)
```

**What it does:** Automatically creates spans for every HTTP request to the FastAPI app. Each request span includes:
- HTTP method, path, status code
- Request/response headers (configurable)
- Server timing

**Important sequencing:** `setup_tracing()` is called on line 60, `FastAPIInstrumentor.instrument_app(app)` on line 72. The global TracerProvider must be set **before** instrumentation — this ordering is correct. If reversed, the instrumentor would use a no-op tracer.

**Span hierarchy:** An incoming `/api/chat` request creates a FastAPI span as the **root span**. Any `trace_tool_call` / `trace_llm_call` spans created during that request become **child spans** automatically (via OTEL context propagation in the same async task).

---

## 7. Auto-instrumentation: HTTPX

**Declared dependency:** `opentelemetry-instrumentation-httpx>=0.60b1` (pyproject.toml line 14)

**Actual usage in code: NONE.**

The HTTPX instrumentation package is **installed but never activated**. There is no call to `HTTPXClientInstrumentor().instrument()` anywhere in the codebase. The `OpenEMRClient` (which uses `httpx.AsyncClient`) is **not auto-instrumented**.

**What this means:** Outbound HTTP calls to OpenEMR (FHIR reads, writes, auth token requests) do **not** generate their own spans. They are invisible in Jaeger unless wrapped by the tool-call decorator layer above them.

**This is a significant gap worth noting.** To activate it, one would add:

```python
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
HTTPXClientInstrumentor().instrument()
```

…in `main.py` after `setup_tracing()`. This would auto-create spans for every `httpx` request with HTTP method, URL, status code, timing.

---

## 8. Decorator Usage — The Wiring Gap

**Critical finding:** The three decorators are **defined but never applied** to any function in the codebase.

Evidence:
- `grep -r "trace_tool_call\|trace_llm_call\|trace_verification" src/` returns only the definitions in `tracing.py`.
- `loop.py` defines a `Tracer` protocol (lines 45–49) but the `AgentLoop.__init__` `tracer` parameter defaults to `None` and is never passed a value from `main.py`.
- The verification functions in `checks.py` are plain functions — no decorator applied.
- The tool functions in `registry.py` are plain lambdas — no decorator applied.

**The observability infrastructure is fully built but not wired in.** The only active tracing comes from:
1. `FastAPIInstrumentor` (HTTP request spans)
2. `ConsoleSpanExporter` / OTLP exporter (exporting those spans)

The custom domain-specific spans (tool calls, LLM latency, verification pass/fail) are **not being emitted in production**.

### How it *would* be wired

To use `trace_tool_call`, you'd apply it to the tool functions:

```python
tracer = setup_tracing()

@trace_tool_call(tracer)
async def fhir_read(resource_type: str, params: dict | None = None) -> dict:
    ...
```

Or in the agent loop, wrapping `_call_llm`:

```python
@trace_llm_call(tracer)
async def _call_llm(self, session):
    ...
```

---

## 9. Jaeger Infrastructure

**File:** `docker-compose.yml:66-76`

```yaml
jaeger:
  image: jaegertracing/jaeger:latest
  restart: unless-stopped
  ports:
    - "16686:16686"    # Jaeger UI
    - "4317:4317"      # OTLP gRPC receiver
    - "4318:4318"      # OTLP HTTP receiver
  environment:
    COLLECTOR_OTLP_ENABLED: "true"
  networks:
    - emr-net
```

The agent service connects via:

```yaml
agent:
  environment:
    OTEL_EXPORTER_OTLP_ENDPOINT: http://jaeger:4317
```

**Port mapping:**
| Port | Protocol | Purpose |
|---|---|---|
| 16686 | HTTP | Jaeger UI (browser) |
| 4317 | gRPC | OTLP trace receiver (used by the agent) |
| 4318 | HTTP | OTLP HTTP receiver (unused but available) |

**All-in-one image:** `jaegertracing/jaeger:latest` is the all-in-one image — collector, storage (in-memory), query, and UI in one container. No persistent storage; **traces are lost on container restart**.

**Network:** Both `agent` and `jaeger` are on `emr-net` bridge network, so the agent resolves `jaeger` by container name.

**README documentation (line 168–173):**
```
Traces are exported via OpenTelemetry to Jaeger:
- **Jaeger UI**: http://localhost:16686
- **OTLP gRPC**: localhost:4317
- **OTLP HTTP**: localhost:4318
```

---

## 10. Span Naming Conventions

| Decorator | Span name pattern | Example |
|---|---|---|
| `trace_tool_call` | `tool.{func.__name__}` | `tool.fhir_read` |
| `trace_llm_call` | `llm.{func.__name__}` | `llm._call_llm` |
| `trace_verification` | `verification.{func.__name__}` | `verification.check_grounding` |

Dotted prefix convention makes spans filterable by category in Jaeger's search UI.

---

## 11. Testing

**There are zero tests for the tracing module.** The `tests/` directory contains unit tests for models, verification, and tools — but no tracing tests. No mocking of TracerProvider, no assertion on span attributes.

This is consistent with the "built but not wired" pattern. Testing tracing typically requires the OTEL test SDK (`InMemorySpanExporter`).

---

## 12. Summary of Gaps / Edge Cases for the Author

1. **Decorators defined but unused** — the most important observation. The infrastructure anticipates a full trace hierarchy but the wiring step was never completed.

2. **HTTPX instrumentation installed but not activated** — another gap between intent and implementation.

3. **Silent fallback to ConsoleSpanExporter** — no logging when OTLP exporter fails. Operators could believe they're sending to Jaeger when they're printing to stdout.

4. **`_set_llm_attributes` duck-typing** — handles both Anthropic SDK objects and dicts, which is good for testability but means the type contract is implicit.

5. **Positional args not captured by `trace_tool_call`** — only `kwargs` are serialised to `tool.arguments`. If a tool function is called with positional args, they're invisible in the span.

6. **`trace_verification` on a list result** — `verification.passed` attribute is silently absent (not `False`, just missing). A subtle difference for anyone querying Jaeger spans.

7. **Jaeger in-memory storage** — traces are ephemeral. No Elasticsearch/Cassandra backend is configured.

8. **`tracer` returned from `setup_tracing()` is captured in `main.py` but never passed to decorators or the agent loop** — it's a dead variable except for its side effect of setting the global provider.

---

## 13. Suggested Examples for the Text

### Example 1: The tracing bootstrap (show the fallback pattern)
Show `setup_tracing()` with annotation explaining the try/except import pattern and Console fallback.

### Example 2: `trace_tool_call` as the canonical decorator
Full code of the decorator with callouts on: span naming, JSON serialization of args, success/failure attributes, exception recording, async/sync dual support.

### Example 3: `_set_llm_attributes` duck-typing
Show the helper to illustrate how domain-specific span attributes are extracted from heterogeneous return types (Anthropic SDK vs dict).

### Example 4: FastAPI auto-instrumentation one-liner
Show the `FastAPIInstrumentor.instrument_app(app)` call in context with `setup_tracing()` to illustrate how the global TracerProvider enables zero-config span creation for HTTP endpoints.

### Example 5: docker-compose Jaeger service
Show the service definition to illustrate the infrastructure side: ports, OTLP enablement, env var wiring.

### Example 6 (edge case): The wiring gap
Show that `tracer` is created but never passed to any decorator. This is a good "infrastructure-ready but not connected" teaching moment — illustrating how OTEL's design separates setup from instrumentation.
