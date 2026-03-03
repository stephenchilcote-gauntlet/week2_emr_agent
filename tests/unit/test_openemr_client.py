from __future__ import annotations

import time
from unittest.mock import AsyncMock

import httpx
import pytest

from src.tools.openemr_client import OpenEMRClient


def _http_response(status_code: int, payload: dict) -> httpx.Response:
    request = httpx.Request("GET", "https://example.com")
    return httpx.Response(status_code=status_code, json=payload, request=request)


@pytest.mark.asyncio
async def test_ensure_auth_sets_30_second_expiry_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="client-id",
        client_secret="secret",
    )
    client._http.post = AsyncMock(
        return_value=_http_response(200, {"access_token": "token-1", "expires_in": 120})
    )
    monkeypatch.setattr(time, "time", lambda: 1_000.0)

    await client._ensure_auth()

    assert client._access_token == "token-1"
    assert client._token_expires == pytest.approx(1_090.0)


@pytest.mark.asyncio
async def test_ensure_auth_reuses_unexpired_token_without_network_call() -> None:
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="client-id",
        client_secret="secret",
    )
    client._access_token = "existing"
    client._token_expires = time.time() + 300
    client._http.post = AsyncMock()

    await client._ensure_auth()

    client._http.post.assert_not_called()


@pytest.mark.asyncio
async def test_fhir_read_retries_once_on_401_after_reauth() -> None:
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="client-id",
        client_secret="secret",
    )
    client._ensure_auth = AsyncMock()

    unauthorized = _http_response(401, {"error": "expired"})
    success = _http_response(200, {"resourceType": "Bundle", "total": 1})
    client._http.get = AsyncMock(side_effect=[unauthorized, success])

    result = await client.fhir_read("Patient", {"identifier": "pat-1"})

    assert result["total"] == 1
    assert client._ensure_auth.await_count == 2


@pytest.mark.asyncio
async def test_api_call_retries_once_on_401_after_reauth() -> None:
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="client-id",
        client_secret="secret",
    )
    client._ensure_auth = AsyncMock()

    unauthorized = _http_response(401, {"error": "expired"})
    success = _http_response(200, {"ok": True})
    client._http.get = AsyncMock(side_effect=[unauthorized, success])

    result = await client.api_call("patient/1", method="GET")

    assert result == {"ok": True}
    assert client._ensure_auth.await_count == 2



# ------------------------------------------------------------------
# api_call edge cases
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_call_unsupported_method_returns_error() -> None:
    """Unsupported HTTP method returns an error dict without raising."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="client-id",
        client_secret="secret",
    )
    client._ensure_auth = AsyncMock()

    result = await client.api_call("patient/1", method="PATCH")

    assert "error" in result
    assert "PATCH" in result["error"]


@pytest.mark.asyncio
async def test_api_call_http_error_returns_error_dict() -> None:
    """Non-401 HTTP errors return an error dict with status_code, not an exception."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="client-id",
        client_secret="secret",
    )
    client._ensure_auth = AsyncMock()
    client._http.get = AsyncMock(return_value=_http_response(500, {"error": "server error"}))

    result = await client.api_call("patient/1")

    assert "error" in result
    assert result.get("status_code") == 500


@pytest.mark.asyncio
async def test_api_call_network_error_returns_error_dict() -> None:
    """Network failures (RequestError) return an error dict, not an exception."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="client-id",
        client_secret="secret",
    )
    client._ensure_auth = AsyncMock()
    client._http.get = AsyncMock(
        side_effect=httpx.RequestError("Connection refused")
    )

    result = await client.api_call("patient/1")

    assert "error" in result
    assert "Connection refused" in result["error"]


@pytest.mark.asyncio
async def test_api_call_post_sends_json_payload() -> None:
    """POST calls send the payload as JSON body."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="client-id",
        client_secret="secret",
    )
    client._ensure_auth = AsyncMock()
    client._http.post = AsyncMock(return_value=_http_response(200, {"uuid": "new-1"}))

    result = await client.api_call(
        "patient/1/medical_problem",
        method="POST",
        payload={"title": "Diabetes", "diagnosis": "ICD10:E11.9"},
    )

    assert result == {"uuid": "new-1"}
    client._http.post.assert_called_once()
    _, call_kwargs = client._http.post.call_args
    assert call_kwargs.get("json") == {"title": "Diabetes", "diagnosis": "ICD10:E11.9"}


@pytest.mark.asyncio
async def test_api_call_url_construction() -> None:
    """api_call constructs the correct URL for the given endpoint."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="",  # No auth
    )
    client._http.get = AsyncMock(return_value=_http_response(200, {"data": []}))

    await client.api_call("patient")

    call_args = client._http.get.call_args
    url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
    assert url == "https://example.com/apis/default/api/patient"


@pytest.mark.asyncio
async def test_api_call_leading_slash_stripped() -> None:
    """api_call strips a leading slash from the endpoint."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="",
    )
    client._http.get = AsyncMock(return_value=_http_response(200, {"data": []}))

    await client.api_call("/patient")

    call_args = client._http.get.call_args
    url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
    # Should NOT have double slashes
    assert "//patient" not in url
    assert url.endswith("/patient")


# ------------------------------------------------------------------
# fhir_read edge cases
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fhir_read_http_error_returns_error_dict() -> None:
    """Non-401 HTTP errors in fhir_read return an error dict."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="client-id",
        client_secret="secret",
    )
    client._ensure_auth = AsyncMock()
    client._http.get = AsyncMock(
        return_value=_http_response(403, {"error": "forbidden"})
    )

    result = await client.fhir_read("Patient", {"_id": "123"})

    assert "error" in result
    assert result.get("status_code") == 403


@pytest.mark.asyncio
async def test_fhir_read_network_error_returns_error_dict() -> None:
    """Network failures in fhir_read return an error dict."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="client-id",
        client_secret="secret",
    )
    client._ensure_auth = AsyncMock()
    client._http.get = AsyncMock(side_effect=httpx.RequestError("timeout"))

    result = await client.fhir_read("Patient")

    assert "error" in result


# ------------------------------------------------------------------
# get_fhir_metadata
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_fhir_metadata_returns_capability_statement() -> None:
    """get_fhir_metadata returns the parsed JSON response."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="",
    )
    capability = {"resourceType": "CapabilityStatement", "fhirVersion": "4.0.1"}
    client._http.get = AsyncMock(return_value=_http_response(200, capability))

    result = await client.get_fhir_metadata()

    assert result["resourceType"] == "CapabilityStatement"
    assert result["fhirVersion"] == "4.0.1"


@pytest.mark.asyncio
async def test_get_fhir_metadata_http_error_returns_error_dict() -> None:
    """HTTP error in get_fhir_metadata returns error dict."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="",
    )
    client._http.get = AsyncMock(return_value=_http_response(503, {"error": "service unavailable"}))

    result = await client.get_fhir_metadata()

    assert "error" in result
    assert result.get("status_code") == 503


# ------------------------------------------------------------------
# Auth edge cases
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_auth_skips_when_no_client_id() -> None:
    """_ensure_auth is a no-op when client_id is empty."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="",  # No client ID
    )
    client._http.post = AsyncMock()

    await client._ensure_auth()

    client._http.post.assert_not_called()
    assert client._access_token is None


@pytest.mark.asyncio
async def test_ensure_auth_clears_token_on_failure() -> None:
    """When auth request fails, the access token is cleared."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="client-id",
        client_secret="secret",
    )
    # Pre-set a token
    client._access_token = "old-token"
    client._token_expires = 0  # Force re-auth

    client._http.post = AsyncMock(
        return_value=_http_response(401, {"error": "invalid_client"})
    )

    await client._ensure_auth()

    assert client._access_token is None


# ------------------------------------------------------------------
# api_call PUT and DELETE
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_call_put_sends_json_payload() -> None:
    """PUT calls send the payload as JSON body."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="client-id",
        client_secret="secret",
    )
    client._ensure_auth = AsyncMock()
    client._http.put = AsyncMock(return_value=_http_response(200, {"updated": True}))

    result = await client.api_call(
        "patient/1/medical_problem/123",
        method="PUT",
        payload={"title": "Updated Diabetes"},
    )

    assert result == {"updated": True}
    client._http.put.assert_called_once()
    _, call_kwargs = client._http.put.call_args
    assert call_kwargs.get("json") == {"title": "Updated Diabetes"}


@pytest.mark.asyncio
async def test_api_call_delete_sends_request() -> None:
    """DELETE calls do not include a JSON body."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="client-id",
        client_secret="secret",
    )
    client._ensure_auth = AsyncMock()
    client._http.delete = AsyncMock(return_value=_http_response(200, {"deleted": True}))

    result = await client.api_call("patient/1/allergy/456", method="DELETE")

    assert result == {"deleted": True}
    client._http.delete.assert_called_once()


@pytest.mark.asyncio
async def test_api_call_put_http_error_returns_error_dict() -> None:
    """Non-2xx response on PUT returns error dict with status_code."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="client-id",
        client_secret="secret",
    )
    client._ensure_auth = AsyncMock()
    client._http.put = AsyncMock(return_value=_http_response(422, {"error": "validation failed"}))

    result = await client.api_call("patient/1/allergy/99", method="PUT", payload={"x": 1})

    assert "error" in result
    assert result.get("status_code") == 422


@pytest.mark.asyncio
async def test_api_call_post_retries_once_on_401_after_reauth() -> None:
    """POST 401 also triggers token refresh and retry."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="client-id",
        client_secret="secret",
    )
    client._ensure_auth = AsyncMock()

    unauthorized = _http_response(401, {"error": "expired"})
    success = _http_response(200, {"uuid": "new-1"})
    client._http.post = AsyncMock(side_effect=[unauthorized, success])

    result = await client.api_call("patient", method="POST", payload={"fname": "John"})

    assert result == {"uuid": "new-1"}
    assert client._ensure_auth.await_count == 2


# ------------------------------------------------------------------
# _auth_headers
# ------------------------------------------------------------------


def test_auth_headers_with_token() -> None:
    """_auth_headers returns Authorization header when a token is set."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="client-id",
    )
    client._access_token = "test-token-abc"

    headers = client._auth_headers()

    assert headers == {"Authorization": "Bearer test-token-abc"}


def test_auth_headers_without_token() -> None:
    """_auth_headers returns an empty dict when no token is set."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="client-id",
    )
    # Default: _access_token is None

    headers = client._auth_headers()

    assert headers == {}


# ------------------------------------------------------------------
# close()
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_calls_aclose() -> None:
    """close() delegates to the underlying httpx AsyncClient."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="",
    )
    client._http.aclose = AsyncMock()

    await client.close()

    client._http.aclose.assert_called_once()


# ------------------------------------------------------------------
# ensure_auth — missing access_token key in response
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_auth_handles_missing_access_token_key() -> None:
    """Response missing 'access_token' key clears token (KeyError handled)."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="client-id",
        client_secret="secret",
    )
    # HTTP 200 but response body has no "access_token" key
    client._http.post = AsyncMock(
        return_value=_http_response(200, {"token_type": "bearer"})
    )

    await client._ensure_auth()

    assert client._access_token is None


# ------------------------------------------------------------------
# fhir_read URL construction
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fhir_read_constructs_correct_url() -> None:
    """fhir_read GETs {fhir_url}/{resource_type}."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="",
    )
    client._http.get = AsyncMock(
        return_value=_http_response(200, {"resourceType": "Patient"})
    )

    await client.fhir_read("Patient/123")

    call_args = client._http.get.call_args
    url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
    assert url == "https://example.com/fhir/Patient/123"


@pytest.mark.asyncio
async def test_fhir_read_passes_query_params() -> None:
    """fhir_read forwards params dict as query string."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="",
    )
    client._http.get = AsyncMock(
        return_value=_http_response(200, {"resourceType": "Bundle", "total": 0})
    )

    await client.fhir_read("Patient", {"_id": "uuid-123", "category": "laboratory"})

    call_args = client._http.get.call_args
    passed_params = call_args.kwargs.get("params") or (call_args.args[1] if len(call_args.args) > 1 else None)
    # params is passed as keyword argument
    assert call_args.kwargs.get("params") == {"_id": "uuid-123", "category": "laboratory"}


@pytest.mark.asyncio
async def test_fhir_read_without_params() -> None:
    """fhir_read works when params is None (no query string)."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="",
    )
    client._http.get = AsyncMock(
        return_value=_http_response(200, {"resourceType": "CapabilityStatement"})
    )

    result = await client.fhir_read("metadata")

    assert result["resourceType"] == "CapabilityStatement"


# ------------------------------------------------------------------
# get_fhir_metadata — network error
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_fhir_metadata_network_error_returns_error_dict() -> None:
    """Network failure in get_fhir_metadata returns error dict."""
    client = OpenEMRClient(
        base_url="https://example.com",
        fhir_url="https://example.com/fhir",
        client_id="",
    )
    client._http.get = AsyncMock(side_effect=httpx.RequestError("timeout"))

    result = await client.get_fhir_metadata()

    assert "error" in result
    assert "timeout" in result["error"]


# ------------------------------------------------------------------
# base_url / fhir_url trailing slash stripping
# ------------------------------------------------------------------


def test_base_url_trailing_slash_is_stripped() -> None:
    """Trailing slashes on base_url are removed to avoid double-slash URLs."""
    client = OpenEMRClient(
        base_url="https://example.com/",
        fhir_url="https://example.com/fhir/",
        client_id="",
    )
    assert not client.base_url.endswith("/")
    assert not client.fhir_url.endswith("/")
