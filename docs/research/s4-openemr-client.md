# §4 Research Notes — The OpenEMR Client

## 1. Primary Source Files

| File | Role |
|------|------|
| `src/tools/openemr_client.py` | The sole file: `OpenEMRClient` class — async HTTP wrapper, OAuth2 auth, FHIR R4 helpers, generic REST helper, lifecycle |
| `src/api/main.py:34-57` | Instantiation inside the FastAPI lifespan; wired to `app.state` |
| `src/tools/registry.py:59-61` | Consumed by `ToolRegistry`; forwarded to standalone tool functions |
| `src/agent/loop.py:26-42` | `Protocol` definition that the agent loop codes against (structural typing) |
| `docker-compose.yml:57-58` | Canonical URLs: `http://openemr:80` (base) and `http://openemr:80/apis/default/fhir` (FHIR) |
| `tests/conftest.py:47-54` | `mock_openemr_client` fixture using `AsyncMock` |
| `tests/unit/test_tools.py:186-198` | Integration test for `fhir_read` through the registry |
| `pyproject.toml:10` | `httpx>=0.28.1` — the underlying HTTP library |
| `docker-compose.yml:38-39` | OpenEMR container env: `OE_USER=admin`, `OE_PASS=pass` (hardcoded in client) |

---

## 2. Class Overview

**Source:** `src/tools/openemr_client.py:8-149`

```python
class OpenEMRClient:
    """Async HTTP client for the OpenEMR REST / FHIR APIs."""

    def __init__(self, base_url: str, fhir_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.fhir_url = fhir_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=30.0)
        self._access_token: str | None = None
        self._token_expires: float = 0
```

### Constructor Details

- **Two base URLs**: The class takes separate `base_url` (for REST API) and `fhir_url` (for FHIR R4 endpoint). Both are `.rstrip("/")`-normalized.
- **Single `httpx.AsyncClient`**: Created once with a 30-second timeout; reused across all requests. This gives connection pooling and keep-alive for free.
- **Token state**: Two private fields: `_access_token` (the Bearer token string or `None`) and `_token_expires` (epoch float, initialized to 0 so the first call always triggers auth).
- **No constructor auth**: Authentication is lazy — deferred to the first API call.

### Design note for author
The class is intentionally thin (149 lines). It does not implement retries, rate limiting, or connection error recovery. Every method catches exceptions and returns `{"error": ...}` dicts rather than raising — the agent loop never sees raw HTTP exceptions.

---

## 3. OAuth2 Password-Grant Authentication

**Source:** `src/tools/openemr_client.py:22-47`

```python
async def _ensure_auth(self) -> None:
    """Obtain or refresh an OAuth2 access token from OpenEMR."""
    if self._access_token and time.time() < self._token_expires:
        return                                          # ① cache hit

    token_url = f"{self.base_url}/oauth2/default/token" # ② endpoint
    form_data = {
        "grant_type": "password",                       # ③ grant type
        "username": "admin",
        "password": "pass",
        "client_id": "site",
        "scope": "openid fhirUser api:oemr api:fhir",
    }

    try:
        resp = await self._http.post(token_url, data=form_data)  # ④ form-encoded POST
        resp.raise_for_status()
        body = resp.json()
        self._access_token = body["access_token"]
        expires_in = int(body.get("expires_in", 3600))
        self._token_expires = time.time() + expires_in - 30  # ⑤ 30s buffer
    except (httpx.HTTPStatusError, httpx.RequestError, KeyError) as exc:
        self._access_token = None                       # ⑥ reset on failure
        self._token_expires = 0
        print(f"[OpenEMRClient] auth failed: {exc}")    # ⑦ log, don't crash
```

### Key Points

1. **Cache-check guard (①)**: Two conditions — token exists AND current time is before the adjusted expiry. If both are true, return immediately (no network call). This makes repeated `fhir_read` calls cheap.

2. **Token endpoint (②)**: `{base_url}/oauth2/default/token` — standard OpenEMR OAuth2 path. The `/default/` segment is OpenEMR's default site identifier.

3. **Grant type (③)**: `password` grant — the simplest OAuth2 flow. The user credentials are sent directly. Appropriate here because the agent is a trusted server-side component talking to a local OpenEMR instance.

4. **Form-encoded body (④)**: Uses `data=form_data` (not `json=`), producing `application/x-www-form-urlencoded` — required by the OAuth2 spec (RFC 6749 §4.3.2).

5. **30-second early-expiry buffer (⑤)**: `time.time() + expires_in - 30`. If the server returns `expires_in=3600`, the client treats the token as expired 30 seconds early — at 3570 seconds. This prevents the race where a request is sent with a token that expires mid-flight. The 30s value covers network latency and clock skew.

6. **Failure reset (⑥)**: On any error, BOTH `_access_token` and `_token_expires` are reset to their initial states (`None` / `0`). This means the next API call will attempt auth again — effectively an automatic retry on the next request.

7. **No crash (⑦)**: Auth failure is logged with `print()` (not `logging`) and swallowed. The comment says "Allow callers to set token manually" — suggesting a design intent for manual token injection in testing, though this path is not used in production code.

### Edge Cases & Surprises

- **Hardcoded credentials**: `username="admin"`, `password="pass"`, `client_id="site"` are baked into the method. Not read from config or env vars. The docker-compose confirms these match: `OE_USER=admin`, `OE_PASS=pass`. This is a **dev/demo convenience**, not production-grade.

- **No `refresh_token` usage**: The code never stores or uses a `refresh_token`. On expiry, it simply re-authenticates with the password grant. This is fine for a server-side agent but differs from typical OAuth2 refresh flows.

- **Default `expires_in`**: `body.get("expires_in", 3600)` — falls back to 1 hour if the field is missing from the response. OpenEMR typically returns 3600.

- **`time.time()` vs monotonic**: Uses wall-clock time. If the system clock jumps backward (NTP correction), the token could appear unexpired when it actually is. A pedantic concern, but worth noting.

- **Thread safety**: There's no lock around `_ensure_auth`. In theory, two concurrent `await _ensure_auth()` calls could race and both POST to the token endpoint. In practice, Python's GIL and the asyncio event loop make this unlikely within a single-process deployment, but it's not formally safe for multi-worker setups.

---

## 4. Header Injection

**Source:** `src/tools/openemr_client.py:49-53`

```python
def _auth_headers(self) -> dict[str, str]:
    headers: dict[str, str] = {}
    if self._access_token:
        headers["Authorization"] = f"Bearer {self._access_token}"
    return headers
```

- Synchronous (not `async`), called after `_ensure_auth()`.
- Returns an empty dict if no token — so unauthenticated requests are possible (they'll get 401 from the server).
- The `Bearer` prefix follows RFC 6750.

---

## 5. FHIR R4 Helpers

### 5a. `fhir_read` — GET

**Source:** `src/tools/openemr_client.py:59-74`

```python
async def fhir_read(
    self, resource_type: str, params: dict | None = None
) -> dict:
    """GET {fhir_url}/{resource_type} with optional query params."""
    await self._ensure_auth()
    url = f"{self.fhir_url}/{resource_type}"
    try:
        resp = await self._http.get(
            url, params=params, headers=self._auth_headers()
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        return {"error": str(exc), "status_code": exc.response.status_code}
    except httpx.RequestError as exc:
        return {"error": str(exc)}
```

**URL construction**: `{fhir_url}/{resource_type}` — e.g., `http://openemr:80/apis/default/fhir/Patient`. The `resource_type` is a bare FHIR type name, not a full path.

**Query params**: Passed as `params=` (httpx will URL-encode them). For the Maria Santos example:
```
GET /apis/default/fhir/Patient?name=Santos
GET /apis/default/fhir/Observation?patient=patient-42&code=4548-4
```

**Error handling pattern**: Two-tier catch:
1. `HTTPStatusError` — server returned 4xx/5xx → returns `{"error": ..., "status_code": N}`
2. `RequestError` — network/timeout error → returns `{"error": ...}` (no status_code)

This pattern is **identical across all four API methods**. The dict-based error return means the agent loop always receives a JSON-serializable dict, never an exception.

### 5b. `fhir_write` — POST

**Source:** `src/tools/openemr_client.py:76-91`

```python
async def fhir_write(
    self, resource_type: str, payload: dict
) -> dict:
    """POST {fhir_url}/{resource_type} with a JSON body."""
    await self._ensure_auth()
    url = f"{self.fhir_url}/{resource_type}"
    try:
        resp = await self._http.post(
            url, json=payload, headers=self._auth_headers()
        )
        resp.raise_for_status()
        return resp.json()
    ...
```

- Always POST (creates a new resource). No PUT/PATCH for updates at the FHIR layer — updates go through `api_call`.
- Uses `json=payload` (not `data=`), so the Content-Type is `application/json`.

---

## 6. Generic REST API Helper

**Source:** `src/tools/openemr_client.py:97-125`

```python
async def api_call(
    self,
    endpoint: str,
    method: str = "GET",
    payload: dict | None = None,
) -> dict:
    """Call {base_url}/apis/default/api/{endpoint}."""
    await self._ensure_auth()
    url = f"{self.base_url}/apis/default/api/{endpoint.lstrip('/')}"
    headers = self._auth_headers()

    try:
        if method.upper() == "GET":
            resp = await self._http.get(url, headers=headers, params=payload)
        elif method.upper() == "POST":
            resp = await self._http.post(url, json=payload, headers=headers)
        elif method.upper() == "PUT":
            resp = await self._http.put(url, json=payload, headers=headers)
        elif method.upper() == "DELETE":
            resp = await self._http.delete(url, headers=headers)
        else:
            return {"error": f"Unsupported HTTP method: {method}"}
        ...
```

### Key Differences from FHIR Helpers

- **URL base**: Uses `{base_url}/apis/default/api/` (OpenEMR's proprietary REST API) rather than the FHIR endpoint.
- **`endpoint.lstrip('/')`**: Prevents double-slash if the caller includes a leading `/`.
- **Method dispatch**: Supports GET, POST, PUT, DELETE. GET passes `payload` as query params; POST/PUT sends as JSON body; DELETE ignores payload.
- **Dual role of `payload`**: For GET, it's query parameters. For POST/PUT, it's a JSON body. This overloading is a convenience but could surprise callers who pass body data for a GET.

### Usage in the Agent Loop

The `loop.py` agent calls this as `api_request` (Protocol method name), but the concrete class implements it as `api_call`. **There's a naming mismatch:**

```python
# loop.py:37 — Protocol defines:
async def api_request(self, endpoint, method, payload) -> dict: ...

# openemr_client.py:97 — Implementation:
async def api_call(self, endpoint, method, payload) -> dict: ...
```

This works because the agent loop's `OpenEMRClient` is a Protocol (structural typing), and `loop.py` line 195 calls `self.openemr_client.api_request(...)` — but the conftest mock patches `api_call`. Worth noting as a potential gotcha.

---

## 7. FHIR Metadata / CapabilityStatement

**Source:** `src/tools/openemr_client.py:131-142`

```python
async def get_fhir_metadata(self) -> dict:
    """GET {fhir_url}/metadata — FHIR CapabilityStatement."""
    await self._ensure_auth()
    url = f"{self.fhir_url}/metadata"
    ...
```

Used only in the health-check endpoint (`src/api/main.py:200-208`) and the `/api/fhir/metadata` passthrough route. Not exposed as an agent tool.

---

## 8. Lifecycle

**Source:** `src/tools/openemr_client.py:148-149`

```python
async def close(self) -> None:
    await self._http.aclose()
```

Called in the FastAPI lifespan teardown (`src/api/main.py:57`):
```python
yield
await openemr_client.close()
```

Note: `httpx.AsyncClient.aclose()` is the correct async cleanup method. Without it, the connection pool would leak on shutdown.

---

## 9. Instantiation & Wiring

**Source:** `src/api/main.py:32-57`

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    base_url = os.environ.get("OPENEMR_BASE_URL", "http://localhost:80")
    fhir_url = os.environ.get(
        "OPENEMR_FHIR_URL", "http://localhost:80/apis/default/fhir"
    )
    openemr_client = OpenEMRClient(base_url=base_url, fhir_url=fhir_url)
    tool_registry = ToolRegistry(openemr_client)
    register_default_tools(tool_registry)
    ...
    app.state.openemr_client = openemr_client
```

- URLs come from environment variables with localhost defaults.
- Single `OpenEMRClient` instance shared across the entire application.
- Wired into: `ToolRegistry` (for tool execution), `AgentLoop` (for direct calls from the loop), and `app.state` (for health-check / metadata endpoints).

---

## 10. The Protocol in the Agent Loop

**Source:** `src/agent/loop.py:26-42`

```python
class OpenEMRClient(Protocol):
    async def fhir_read(self, resource_type: str, params: ...) -> dict: ...
    async def fhir_write(self, resource_type: str, payload: ...) -> dict: ...
    async def api_request(self, endpoint: str, method: str, payload: ...) -> dict: ...
```

The agent loop uses **structural subtyping** (PEP 544 Protocol). It never imports `OpenEMRClient` from `src/tools/openemr_client`. This means:
- The concrete client class doesn't need to inherit from or register with the Protocol.
- Tests can pass any `AsyncMock` that has the right method signatures.
- **But**: The Protocol declares `api_request`, while the concrete class implements `api_call` — a naming discrepancy. In production, `loop.py:195` calls `self.openemr_client.api_request(...)`, which would fail at runtime against the real `OpenEMRClient`. (The tests mock the method, so they pass.)

---

## 11. Suggested Running Example — Maria Santos

### Context from Seed Data

**Source:** `docker/seed_data.sql`

Maria Santos (pid=1) has:
- DOB: 1985-03-14, Female
- Diagnoses: Type 2 Diabetes (E11.9), Essential Hypertension (I10)
- Medications: Metformin 500mg twice daily, Lisinopril 10mg daily
- Lab results: HbA1c trending: 7.8% (Jan 2025) → 8.2% (Jul 2025), LOINC code 4548-4

### Example 1: Fetching Patient Demographics

```python
# What happens when the agent calls fhir_read("Patient", {"name": "Santos"})

# Step 1: _ensure_auth() fires (first call, no cached token)
#   POST http://openemr:80/oauth2/default/token
#   Body: grant_type=password&username=admin&password=pass&client_id=site&scope=...
#   Response: {"access_token": "eyJ...", "expires_in": 3600, ...}
#   _access_token = "eyJ..."
#   _token_expires = time.time() + 3600 - 30  →  ~3570 seconds from now

# Step 2: GET http://openemr:80/apis/default/fhir/Patient?name=Santos
#   Authorization: Bearer eyJ...
#   Response: FHIR Bundle with Maria Santos
```

### Example 2: Fetching Observations (Token Cached)

```python
# Second call — e.g. fhir_read("Observation", {"patient": "1", "code": "4548-4"})

# Step 1: _ensure_auth() → cache hit (token exists, time < expires)
#   No HTTP call — returns immediately

# Step 2: GET http://openemr:80/apis/default/fhir/Observation?patient=1&code=4548-4
#   Authorization: Bearer eyJ...  (same token)
#   Response: FHIR Bundle with HbA1c observations (7.8% and 8.2%)
```

### Example 3: Token Expiry Mid-Session

```python
# ~59 minutes into a session, the 30s buffer kicks in:
#   time.time() > _token_expires  (because buffer subtracted 30s)
#
# _ensure_auth() re-authenticates:
#   POST to token endpoint → new access_token
#   Transparent to caller — no error, no retry logic needed
```

---

## 12. Relationship Between `OpenEMRClient` and `ToolRegistry`

The tool functions in `registry.py` are **thin wrappers** that close over the client:

```python
# registry.py:186-190 — lambda captures `client`
registry.register(
    name="fhir_read",
    func=lambda resource_type, params=None: tool_fhir_read(
        client, resource_type, params
    ),
    ...
)
```

```python
# registry.py:117-123 — standalone function calls through to client
async def tool_fhir_read(client: OpenEMRClient, resource_type, params) -> dict:
    return await client.fhir_read(resource_type, params)
```

The flow is: **LLM → tool_call → ToolRegistry.execute() → lambda → tool_fhir_read() → OpenEMRClient.fhir_read() → httpx**.

`fhir_write` adds a **manifest approval check** before delegating to the client:

```python
# registry.py:134-142
if manifest_item_id and registry and registry._pending_manifest:
    item = next(
        (i for i in registry._pending_manifest.items if i.id == manifest_item_id),
        None,
    )
    if item is None:
        return {"error": f"Manifest item '{manifest_item_id}' not found"}
    if not item.approved:
        return {"error": f"Manifest item '{manifest_item_id}' not approved"}
return await client.fhir_write(resource_type, payload)
```

---

## 13. Error-Handling Pattern

Every public method follows the same pattern:

```
await self._ensure_auth()        # may silently fail
url = ...                         # construct URL
try:
    resp = await self._http.{method}(url, ..., headers=self._auth_headers())
    resp.raise_for_status()
    return resp.json()
except httpx.HTTPStatusError:     # 4xx/5xx → dict with error + status_code
    return {"error": ..., "status_code": ...}
except httpx.RequestError:        # network/timeout → dict with error only
    return {"error": ...}
```

**Consequence**: Callers never need try/except. Every return is a `dict`. The LLM sees error information as tool result content, not as a framework exception. This is a deliberate design choice for LLM tool-use: the agent can reason about errors ("I got a 404, let me try a different resource type") instead of crashing.

**Subtle issue**: If `_ensure_auth()` fails silently (prints but doesn't raise), `_auth_headers()` returns `{}`, and the subsequent request will likely get a 401. The 401 is caught by `HTTPStatusError` and returned as `{"error": ..., "status_code": 401}`. So auth failures surface as API errors, not auth errors.

---

## 14. Testing Patterns

### Unit tests mock at the client level

```python
# tests/conftest.py:48-54
client = AsyncMock()
client.fhir_read = AsyncMock(return_value={"resourceType": "Patient", "id": "1"})
client.fhir_write = AsyncMock(return_value={"resourceType": "Condition", "id": "99"})
client.api_call = AsyncMock(return_value={"status": "ok"})
```

### Registry tests mock `_ensure_auth` to avoid real HTTP

```python
# tests/unit/test_tools.py:189-190
client._ensure_auth = AsyncMock()
```

### No integration tests for `_ensure_auth` itself

There are no tests that exercise the OAuth2 flow against a real or mock token endpoint. The auth logic is tested only by the fact that integration/eval tests succeed against the Docker OpenEMR instance.

---

## 15. Edge Cases & Surprising Behaviour Worth Documenting

1. **Hardcoded credentials in source**: `admin/pass/site` are not configurable. Secure only because the Docker setup matches. A production deployment would need to extract these to env vars.

2. **`api_call` vs `api_request` naming mismatch**: The Protocol in `loop.py` declares `api_request`; the implementation in `openemr_client.py` has `api_call`. This would fail at runtime if the agent loop actually called `api_request` on the real client (it uses the registry path for fhir operations, and `api_request` for the non-FHIR API path in `execute_approved`).

3. **`print()` for error logging**: `_ensure_auth` uses `print()` instead of the `logging` module. The rest of the codebase uses `logging` (e.g., `loop.py` has `logger = logging.getLogger(__name__)`).

4. **No retry/backoff**: A transient network error during auth or API call is surfaced immediately. The next call will retry auth (since the token is cleared), but there's no exponential backoff.

5. **`data=` vs `json=`**: Auth uses `data=form_data` (form-encoded, per OAuth2 spec). API calls use `json=payload`. Easy to confuse in code review.

6. **`payload` dual semantics in `api_call`**: For GET, `payload` becomes query params. For POST/PUT, it becomes the JSON body. DELETE ignores it entirely. This overloading is undocumented in the method's docstring.

7. **30-second buffer value**: Hardcoded, not configurable. 30 seconds is generous for local Docker networking but may be tight if the client is talking to a remote OpenEMR over a slow link.

8. **Single `httpx.AsyncClient` for everything**: Auth requests and API requests share the same connection pool and timeout settings. The 30s timeout applies to token requests too — if the token endpoint is slow, it could timeout.

9. **No `Content-Type` header for FHIR**: The FHIR spec recommends `Accept: application/fhir+json`. This client sends no explicit Accept header — it relies on OpenEMR's default JSON response. Would fail against a FHIR server that defaults to XML.

10. **Silently unauthenticated requests**: If auth fails, `_auth_headers()` returns `{}`, and the request proceeds without `Authorization`. The server returns 401, which is returned as `{"error": ..., "status_code": 401}`. The LLM sees this as a tool error. There's no special handling to distinguish "auth failed" from "resource not found".
