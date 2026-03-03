from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from src.tools.openemr_client import OpenEMRClient
from src.tools.registry import (
    ChangeManifest,
    ManifestItem,
    PageContext,
    ToolRegistry,
    register_default_tools,
    tool_fhir_read,
    tool_get_page_context,
    tool_openemr_api,
    tool_send_developer_feedback,
    tool_submit_manifest,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_registry(client: AsyncMock | None = None) -> ToolRegistry:
    if client is None:
        client = AsyncMock(spec=OpenEMRClient)
    return ToolRegistry(openemr_client=client)


# ------------------------------------------------------------------
# ToolRegistry registration and execution
# ------------------------------------------------------------------

class TestToolRegistry:
    @pytest.mark.asyncio
    async def test_register_and_execute(self):
        registry = _make_registry()

        async def my_tool(x: int) -> dict:
            return {"result": x * 2}

        registry.register(
            name="double",
            func=my_tool,
            description="Doubles a number",
            input_schema={
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
            },
        )

        result_json = await registry.execute("double", {"x": 5})
        result = json.loads(result_json)
        assert result == {"result": 10}

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        registry = _make_registry()
        result_json = await registry.execute("nonexistent", {})
        result = json.loads(result_json)
        assert "error" in result
        assert "Unknown tool" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_tool_exception(self):
        registry = _make_registry()

        async def broken_tool() -> dict:
            raise ValueError("something broke")

        registry.register(name="broken", func=broken_tool, description="Breaks")

        result_json = await registry.execute("broken", {})
        result = json.loads(result_json)
        assert "error" in result
        assert "ValueError" in result["error"]

    def test_get_tool_definitions(self):
        registry = _make_registry()

        async def dummy() -> dict:
            return {}

        registry.register(
            name="my_tool",
            func=dummy,
            description="A test tool",
            input_schema={"type": "object", "properties": {"a": {"type": "string"}}},
        )

        defs = registry.get_tool_definitions()
        assert len(defs) == 1
        assert defs[0]["name"] == "my_tool"
        assert defs[0]["description"] == "A test tool"
        assert "properties" in defs[0]["input_schema"]


# ------------------------------------------------------------------
# tool_get_page_context
# ------------------------------------------------------------------

class TestToolGetPageContext:
    @pytest.mark.asyncio
    async def test_returns_context(self):
        registry = _make_registry()
        ctx = PageContext(page="encounter", patient_id="p1", encounter_id="e1")
        registry.set_page_context(ctx)

        result = await tool_get_page_context(registry)
        assert result["patient_id"] == "p1"
        assert result["encounter_id"] == "e1"
        assert result["page"] == "encounter"

    @pytest.mark.asyncio
    async def test_no_context_returns_error(self):
        registry = _make_registry()
        result = await tool_get_page_context(registry)
        assert "error" in result


# ------------------------------------------------------------------
# tool_submit_manifest
# ------------------------------------------------------------------

class TestToolSubmitManifest:
    @pytest.mark.asyncio
    async def test_stores_manifest(self):
        registry = _make_registry()
        manifest_dict = {
            "items": [
                {
                    "id": "item-1",
                    "action": "create",
                    "resource_type": "Condition",
                    "summary": "Add diabetes",
                    "payload": {"code": "E11.9"},
                }
            ]
        }
        result = await tool_submit_manifest(registry, manifest_dict)
        assert result["status"] == "manifest_pending_review"
        assert result["item_count"] == 1
        assert registry._pending_manifest is not None
        assert len(registry._pending_manifest.items) == 1

    @pytest.mark.asyncio
    async def test_invalid_manifest(self):
        registry = _make_registry()
        result = await tool_submit_manifest(registry, {"items": "not-a-list"})
        assert "error" in result


# ------------------------------------------------------------------
# tool_send_developer_feedback
# ------------------------------------------------------------------

class TestToolSendDeveloperFeedback:
    @pytest.mark.asyncio
    async def test_returns_confirmation(self):
        result = await tool_send_developer_feedback("bug", "Search returns 500 on empty query")
        assert result["status"] == "feedback_submitted"
        assert result["category"] == "bug"

    @pytest.mark.asyncio
    async def test_feature_request(self):
        result = await tool_send_developer_feedback("feature_request", "Add dark mode")
        assert result["status"] == "feedback_submitted"
        assert result["category"] == "feature_request"


# ------------------------------------------------------------------
# Default tools wiring
# ------------------------------------------------------------------

class TestDefaultTools:
    def test_register_default_tools(self):
        client = AsyncMock(spec=OpenEMRClient)
        registry = ToolRegistry(openemr_client=client)
        register_default_tools(registry)

        defs = registry.get_tool_definitions()
        names = {d["name"] for d in defs}
        assert "fhir_read" in names
        assert "openemr_api" in names
        assert "get_page_context" in names
        assert "send_developer_feedback" in names
        assert "submit_manifest" in names

    def test_each_definition_has_required_fields(self):
        client = AsyncMock(spec=OpenEMRClient)
        registry = ToolRegistry(openemr_client=client)
        register_default_tools(registry)

        for defn in registry.get_tool_definitions():
            assert "name" in defn
            assert "description" in defn
            assert "input_schema" in defn
            assert isinstance(defn["name"], str)
            assert isinstance(defn["description"], str)
            assert isinstance(defn["input_schema"], dict)

    @pytest.mark.asyncio
    async def test_fhir_read_tool(self):
        client = AsyncMock(spec=OpenEMRClient)
        client.fhir_read = AsyncMock(return_value={
            "resourceType": "Bundle",
            "total": 1,
            "entry": [{"resource": {"resourceType": "Patient", "id": "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"}}],
        })
        # Also mock _ensure_auth so it doesn't try real HTTP
        client._ensure_auth = AsyncMock()

        registry = ToolRegistry(openemr_client=client)
        register_default_tools(registry)

        result_json = await registry.execute("fhir_read", {"resource_type": "Patient"})
        result = json.loads(result_json)
        assert result["resourceType"] == "Bundle"
        assert result["entry"][0]["resource"]["id"] == "bbb13f7a-966e-4c7c-aea5-4bac3ce98505"

    @pytest.mark.asyncio
    async def test_get_page_context_via_registry(self):
        client = AsyncMock(spec=OpenEMRClient)
        registry = ToolRegistry(openemr_client=client)
        register_default_tools(registry)

        # No context set → error
        result_json = await registry.execute("get_page_context", {})
        result = json.loads(result_json)
        assert "error" in result

        # Set context → success
        registry.set_page_context(PageContext(page="summary", patient_id="p1"))
        result_json = await registry.execute("get_page_context", {})
        result = json.loads(result_json)
        assert result["patient_id"] == "p1"

    @pytest.mark.asyncio
    async def test_submit_manifest_via_registry(self):
        client = AsyncMock(spec=OpenEMRClient)
        registry = ToolRegistry(openemr_client=client)
        register_default_tools(registry)

        manifest = {
            "items": [
                {
                    "id": "i1",
                    "action": "create",
                    "resource_type": "Observation",
                    "summary": "Add vitals",
                }
            ]
        }
        result_json = await registry.execute("submit_manifest", {"manifest": manifest})
        result = json.loads(result_json)
        assert result["status"] == "manifest_pending_review"


# ------------------------------------------------------------------
# tool_fhir_read — _count injection
# ------------------------------------------------------------------


class TestToolFhirReadCountInjection:
    @pytest.mark.asyncio
    async def test_list_query_injects_count_1000(self):
        """resource_type without slash injects _count=1000 into params."""
        client = AsyncMock(spec=OpenEMRClient)
        client.fhir_read = AsyncMock(return_value={"resourceType": "Bundle", "total": 5})

        await tool_fhir_read(client, "Patient")

        client.fhir_read.assert_called_once()
        _, call_kwargs = client.fhir_read.call_args
        passed_params = call_kwargs.get("params") or client.fhir_read.call_args.args[1]
        assert passed_params.get("_count") == "1000"

    @pytest.mark.asyncio
    async def test_individual_read_no_count_injected(self):
        """resource_type with slash (e.g. Patient/123) skips _count injection."""
        client = AsyncMock(spec=OpenEMRClient)
        client.fhir_read = AsyncMock(return_value={"resourceType": "Patient", "id": "123"})

        await tool_fhir_read(client, "Patient/123")

        _, call_kwargs = client.fhir_read.call_args
        passed_params = call_kwargs.get("params")
        # No params should be set (or params is None) for individual reads
        assert passed_params is None

    @pytest.mark.asyncio
    async def test_existing_params_merged_with_count(self):
        """Caller-supplied params are preserved and _count is added."""
        client = AsyncMock(spec=OpenEMRClient)
        client.fhir_read = AsyncMock(return_value={"resourceType": "Bundle", "total": 0})

        await tool_fhir_read(client, "Condition", {"category": "problem-list-item"})

        call_args = client.fhir_read.call_args
        # params is passed as positional arg[1] or keyword
        passed_params = (
            call_args.kwargs.get("params")
            if call_args.kwargs.get("params") is not None
            else call_args.args[1] if len(call_args.args) > 1 else None
        )
        assert passed_params is not None
        assert passed_params.get("_count") == "1000"
        assert passed_params.get("category") == "problem-list-item"

    @pytest.mark.asyncio
    async def test_caller_count_not_overridden(self):
        """If caller already provides _count, it is preserved (setdefault)."""
        client = AsyncMock(spec=OpenEMRClient)
        client.fhir_read = AsyncMock(return_value={"resourceType": "Bundle", "total": 0})

        await tool_fhir_read(client, "Patient", {"_count": "50"})

        call_args = client.fhir_read.call_args
        passed_params = (
            call_args.kwargs.get("params")
            if call_args.kwargs.get("params") is not None
            else call_args.args[1] if len(call_args.args) > 1 else None
        )
        assert passed_params is not None
        assert passed_params.get("_count") == "50"  # caller value preserved


# ------------------------------------------------------------------
# tool_openemr_api
# ------------------------------------------------------------------


class TestToolOpenemrApi:
    @pytest.mark.asyncio
    async def test_delegates_get_to_client(self):
        """tool_openemr_api always uses GET method."""
        client = AsyncMock(spec=OpenEMRClient)
        client.api_call = AsyncMock(return_value={"data": []})

        result = await tool_openemr_api(client, "patient")

        client.api_call.assert_called_once_with("patient", "GET")
        assert result == {"data": []}

    @pytest.mark.asyncio
    async def test_passes_endpoint_through(self):
        """Endpoint string is forwarded unchanged."""
        client = AsyncMock(spec=OpenEMRClient)
        client.api_call = AsyncMock(return_value={})

        await tool_openemr_api(client, "patient/4/medical_problem")

        call_args = client.api_call.call_args
        assert call_args.args[0] == "patient/4/medical_problem"


# ------------------------------------------------------------------
# tool_send_developer_feedback — message field
# ------------------------------------------------------------------


class TestToolSendDeveloperFeedbackExtra:
    @pytest.mark.asyncio
    async def test_response_includes_message_field(self):
        result = await tool_send_developer_feedback("bug", "Search fails on empty query")
        assert "message" in result
        assert len(result["message"]) > 0

    @pytest.mark.asyncio
    async def test_response_category_echoed(self):
        result = await tool_send_developer_feedback("improvement", "Add dark mode")
        assert result["category"] == "improvement"
        assert result["status"] == "feedback_submitted"
