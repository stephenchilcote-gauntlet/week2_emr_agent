from __future__ import annotations

import json
import traceback
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from .openemr_client import OpenEMRClient


# ------------------------------------------------------------------
# Lightweight domain models used by the registry
# ------------------------------------------------------------------

class PageContext(BaseModel):
    """Describes the current UI page the clinician is viewing."""
    page: str = ""
    patient_id: str | None = None
    encounter_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ManifestItem(BaseModel):
    id: str
    action: str  # e.g. "create", "update", "delete"
    resource_type: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    approved: bool = False


class ChangeManifest(BaseModel):
    items: list[ManifestItem] = Field(default_factory=list)


# ------------------------------------------------------------------
# Tool metadata (for Anthropic tool-use format)
# ------------------------------------------------------------------

class _ToolEntry(BaseModel):
    name: str
    func: Any  # Callable – stored but not serialised
    description: str
    input_schema: dict[str, Any]

    class Config:
        arbitrary_types_allowed = True


# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------

class ToolRegistry:
    """Maintains the set of tools available to the agent and executes them."""

    def __init__(self, openemr_client: OpenEMRClient) -> None:
        self.client = openemr_client
        self._tools: dict[str, _ToolEntry] = {}
        self._page_context: PageContext | None = None
        self._pending_manifest: ChangeManifest | None = None

    # -- registration ------------------------------------------------

    def register(
        self,
        name: str,
        func: Callable,
        description: str,
        input_schema: dict[str, Any] | None = None,
    ) -> None:
        self._tools[name] = _ToolEntry(
            name=name,
            func=func,
            description=description,
            input_schema=input_schema or {"type": "object", "properties": {}},
        )

    # -- execution ---------------------------------------------------

    async def execute(self, name: str, arguments: dict) -> str:
        """Run a tool by name and return a JSON-encoded result string."""
        entry = self._tools.get(name)
        if entry is None:
            return json.dumps({"error": f"Unknown tool: {name}"})
        try:
            result = await entry.func(**arguments)
            return json.dumps(result, default=str)
        except Exception:
            return json.dumps({"error": traceback.format_exc()})

    # -- context / manifest ------------------------------------------

    def set_page_context(self, ctx: PageContext) -> None:
        self._page_context = ctx

    # -- Anthropic-format definitions --------------------------------

    def get_tool_definitions(self) -> list[dict]:
        """Return tool definitions in Anthropic tool-use schema."""
        return [
            {
                "name": entry.name,
                "description": entry.description,
                "input_schema": entry.input_schema,
            }
            for entry in self._tools.values()
        ]


# ------------------------------------------------------------------
# Standalone tool functions
# ------------------------------------------------------------------

async def tool_fhir_read(
    client: OpenEMRClient,
    resource_type: str,
    params: dict | None = None,
) -> dict:
    """Read FHIR resources (e.g. Patient, Condition, MedicationRequest)."""
    # OpenEMR's FHIR server has a small default page size and reports total
    # as the page count (non-standard). Inject _count so list queries always
    # return the full result set rather than a misleading truncated first page.
    if "/" not in resource_type:
        merged = dict(params or {})
        merged.setdefault("_count", "1000")
        params = merged
    return await client.fhir_read(resource_type, params)


async def tool_openemr_api(
    client: OpenEMRClient,
    endpoint: str,
) -> dict:
    """Call an OpenEMR REST API endpoint (GET only)."""
    return await client.api_call(endpoint, "GET")


async def tool_get_page_context(registry: ToolRegistry) -> dict:
    """Return the current UI page context (patient, encounter, etc.)."""
    if registry._page_context is None:
        return {"error": "No page context available"}
    return registry._page_context.model_dump()


async def tool_submit_manifest(registry: ToolRegistry, manifest: dict) -> dict:
    """Store a change manifest for human review before execution."""
    try:
        parsed = ChangeManifest.model_validate(manifest)
    except Exception as exc:
        return {"error": f"Invalid manifest: {exc}"}
    registry._pending_manifest = parsed
    return {
        "status": "manifest_pending_review",
        "item_count": len(parsed.items),
        "items": [i.model_dump() for i in parsed.items],
    }


# ------------------------------------------------------------------
# Helper: wire up all default tools to a registry instance
# ------------------------------------------------------------------

def register_default_tools(registry: ToolRegistry) -> None:
    """Register the standard tool set on *registry*."""

    client = registry.client

    registry.register(
        name="fhir_read",
        func=lambda resource_type, params=None: tool_fhir_read(
            client, resource_type, params
        ),
        description="Search or read FHIR resources (Patient, Condition, Observation, etc.).",
        input_schema={
            "type": "object",
            "properties": {
                "resource_type": {
                    "type": "string",
                    "description": "FHIR resource type, e.g. 'Patient', 'Condition'.",
                },
                "params": {
                    "type": "object",
                    "description": "Optional query parameters.",
                },
            },
            "required": ["resource_type"],
        },
    )

    registry.register(
        name="openemr_api",
        func=lambda endpoint: tool_openemr_api(client, endpoint),
        description="Read data from OpenEMR REST API endpoints (GET only).",
        input_schema={
            "type": "object",
            "properties": {
                "endpoint": {
                    "type": "string",
                    "description": "API path, e.g. 'patient/1'.",
                },
            },
            "required": ["endpoint"],
        },
    )

    registry.register(
        name="get_page_context",
        func=lambda: tool_get_page_context(registry),
        description="Get the current UI page context (active patient, encounter, etc.).",
        input_schema={"type": "object", "properties": {}},
    )

    registry.register(
        name="submit_manifest",
        func=lambda manifest: tool_submit_manifest(registry, manifest),
        description="Submit a change manifest for human review before writing data.",
        input_schema={
            "type": "object",
            "properties": {
                "manifest": {
                    "type": "object",
                    "description": "Change manifest with items to review.",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "action": {"type": "string"},
                                    "resource_type": {"type": "string"},
                                    "summary": {"type": "string"},
                                    "payload": {"type": "object"},
                                },
                                "required": ["id", "action", "resource_type", "summary"],
                            },
                        }
                    },
                },
            },
            "required": ["manifest"],
        },
    )
