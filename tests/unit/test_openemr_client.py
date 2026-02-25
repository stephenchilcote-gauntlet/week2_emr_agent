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


