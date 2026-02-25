from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import httpx


class OpenEMRClient:
    """Async HTTP client for the OpenEMR REST / FHIR APIs."""

    # Default SMART-on-FHIR scopes for a clinical agent
    DEFAULT_SCOPES = (
        "openid api:oemr api:fhir "
        # FHIR resource scopes
        "user/Patient.read user/Patient.write "
        "user/Condition.read "
        "user/Observation.read "
        "user/MedicationRequest.read "
        "user/Medication.read "
        "user/Encounter.read "
        "user/AllergyIntolerance.read "
        "user/Immunization.read "
        "user/Procedure.read "
        "user/DiagnosticReport.read "
        "user/DocumentReference.read "
        "user/Organization.read "
        "user/Practitioner.read "
        "user/CarePlan.read "
        "user/CareTeam.read "
        "user/Goal.read "
        "user/Provenance.read "
        "user/Coverage.read "
        "user/Device.read "
        "user/Location.read "
        # REST API write scopes (OpenEMR uses lowercase names)
        "user/patient.read user/patient.write "
        "user/medical_problem.read user/medical_problem.write "
        "user/allergy.read user/allergy.write "
        "user/medication.read user/medication.write "
        "user/encounter.read user/encounter.write "
        "user/vital.read user/vital.write"
    )

    def __init__(
        self,
        base_url: str,
        fhir_url: str,
        client_id: str = "",
        client_secret: str = "",
        username: str = "admin",
        password: str = "pass",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.fhir_url = fhir_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._username = username
        self._password = password
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

        if not self._client_id:
            print("[OpenEMRClient] no client_id configured, skipping auth")
            return

        token_url = f"{self.base_url}/oauth2/default/token"
        form_data = {
            "grant_type": "password",
            "username": self._username,
            "password": self._password,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "user_role": "users",
            "scope": self.DEFAULT_SCOPES,
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
            print(f"[OpenEMRClient] auth failed: {exc}")

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    async def _request_with_retry(
        self,
        request_factory: Callable[[], Awaitable[httpx.Response]],
    ) -> httpx.Response:
        """Run a request and retry once when the token has expired."""
        for attempt in range(2):
            response = await request_factory()
            try:
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError:
                if response.status_code == 401 and attempt == 0:
                    self._access_token = None
                    self._token_expires = 0
                    await self._ensure_auth()
                    continue
                raise
        raise RuntimeError("unreachable")

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
            resp = await self._request_with_retry(
                lambda: self._http.get(
                    url,
                    params=params,
                    headers=self._auth_headers(),
                )
            )
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

        try:
            if method.upper() == "GET":
                resp = await self._request_with_retry(
                    lambda: self._http.get(
                        url,
                        headers=self._auth_headers(),
                        params=payload,
                    )
                )
            elif method.upper() == "POST":
                resp = await self._request_with_retry(
                    lambda: self._http.post(
                        url,
                        json=payload,
                        headers=self._auth_headers(),
                    )
                )
            elif method.upper() == "PUT":
                resp = await self._request_with_retry(
                    lambda: self._http.put(
                        url,
                        json=payload,
                        headers=self._auth_headers(),
                    )
                )
            elif method.upper() == "DELETE":
                resp = await self._request_with_retry(
                    lambda: self._http.delete(
                        url,
                        headers=self._auth_headers(),
                    )
                )
            else:
                return {"error": f"Unsupported HTTP method: {method}"}

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
