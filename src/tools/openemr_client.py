from __future__ import annotations

import time

import httpx


class OpenEMRClient:
    """Async HTTP client for the OpenEMR REST / FHIR APIs."""

    def __init__(self, base_url: str, fhir_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.fhir_url = fhir_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=30.0)
        self._access_token: str | None = None
        self._token_expires: float = 0

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def _ensure_auth(self) -> None:
        """Obtain or refresh an OAuth2 access token from OpenEMR."""
        if self._access_token and time.time() < self._token_expires:
            return

        token_url = f"{self.base_url}/oauth2/default/token"
        form_data = {
            "grant_type": "password",
            "username": "admin",
            "password": "pass",
            "client_id": "site",
            "scope": "openid fhirUser api:oemr api:fhir",
        }

        try:
            resp = await self._http.post(token_url, data=form_data)
            resp.raise_for_status()
            body = resp.json()
            self._access_token = body["access_token"]
            expires_in = int(body.get("expires_in", 3600))
            self._token_expires = time.time() + expires_in - 30  # 30s buffer
        except (httpx.HTTPStatusError, httpx.RequestError, KeyError) as exc:
            self._access_token = None
            self._token_expires = 0
            # Allow callers to set token manually; log but don't crash
            print(f"[OpenEMRClient] auth failed: {exc}")

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    # ------------------------------------------------------------------
    # FHIR helpers
    # ------------------------------------------------------------------

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
        except httpx.HTTPStatusError as exc:
            return {"error": str(exc), "status_code": exc.response.status_code}
        except httpx.RequestError as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Generic REST API helper
    # ------------------------------------------------------------------

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

            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            return {"error": str(exc), "status_code": exc.response.status_code}
        except httpx.RequestError as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # FHIR metadata / CapabilityStatement
    # ------------------------------------------------------------------

    async def get_fhir_metadata(self) -> dict:
        """GET {fhir_url}/metadata — FHIR CapabilityStatement."""
        await self._ensure_auth()
        url = f"{self.fhir_url}/metadata"
        try:
            resp = await self._http.get(url, headers=self._auth_headers())
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            return {"error": str(exc), "status_code": exc.response.status_code}
        except httpx.RequestError as exc:
            return {"error": str(exc)}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        await self._http.aclose()
