from __future__ import annotations

import json
import logging
from typing import Any, Protocol

import anthropic

from .models import (
    AgentMessage,
    AgentSession,
    ChangeManifest,
    ManifestAction,
    ManifestItem,
    ToolCall,
    ToolResult,
)
from .prompts import SYSTEM_PROMPT, TOOL_DEFINITIONS

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 15
MODEL = "claude-sonnet-4-20250514"


class OpenEMRClient(Protocol):
    """Protocol for the OpenEMR HTTP client used by the agent."""

    async def fhir_read(
        self, resource_type: str, params: dict[str, str] | None = None
    ) -> dict[str, Any]: ...

    async def fhir_write(
        self, resource_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def api_request(
        self,
        endpoint: str,
        method: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class Tracer(Protocol):
    """Protocol for observability tracer."""

    def start_span(self, name: str, **kwargs: Any) -> Any: ...
    def end_span(self, span: Any) -> None: ...


class ToolsRegistry(Protocol):
    """Protocol for the tools registry that dispatches tool execution."""

    def get_tool_names(self) -> list[str]: ...


class AgentLoop:
    """Core agent loop that orchestrates LLM calls and tool execution."""

    def __init__(
        self,
        anthropic_client: anthropic.AsyncAnthropic,
        openemr_client: OpenEMRClient,
        tools_registry: ToolsRegistry | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self.anthropic_client = anthropic_client
        self.openemr_client = openemr_client
        self.tools_registry = tools_registry
        self.tracer = tracer

    async def run(
        self, session: AgentSession, user_message: str
    ) -> AgentSession:
        """Run the agent loop: process a user message and return updated session.

        The loop calls the LLM, executes any tool calls, and repeats until the
        LLM produces a final text response or submits a manifest for review.
        """
        session.messages.append(
            AgentMessage(role="user", content=user_message)
        )

        for _round in range(MAX_TOOL_ROUNDS):
            response = await self._call_llm(session)

            tool_calls = self._extract_tool_calls(response)
            text_content = self._extract_text(response)

            if not tool_calls:
                session.messages.append(
                    AgentMessage(role="assistant", content=text_content)
                )
                break

            session.messages.append(
                AgentMessage(
                    role="assistant",
                    content=text_content,
                    tool_calls=tool_calls,
                )
            )

            tool_results: list[ToolResult] = []
            for tc in tool_calls:
                result = await self._execute_tool(tc, session)
                tool_results.append(result)

            session.messages.append(
                AgentMessage(
                    role="tool", content="", tool_results=tool_results
                )
            )

            if session.phase == "reviewing":
                break
        else:
            session.messages.append(
                AgentMessage(
                    role="assistant",
                    content=(
                        "I've reached the maximum number of tool calls for "
                        "this turn. Please provide more guidance or simplify "
                        "the request."
                    ),
                )
            )

        return session

    async def _call_llm(
        self, session: AgentSession
    ) -> anthropic.types.Message:
        """Call Claude with the current conversation and tool definitions."""
        messages = self._build_messages(session)
        system_prompt = self._get_system_prompt(session)

        response = await self.anthropic_client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=messages,
            tools=TOOL_DEFINITIONS,
        )

        logger.debug(
            "LLM response: stop_reason=%s, usage=%s",
            response.stop_reason,
            response.usage,
        )

        return response

    async def _execute_tool(
        self, tool_call: ToolCall, session: AgentSession
    ) -> ToolResult:
        """Execute a single tool call and return the result."""
        try:
            if tool_call.name == "fhir_read":
                result = await self.openemr_client.fhir_read(
                    resource_type=tool_call.arguments["resource_type"],
                    params=tool_call.arguments.get("params"),
                )
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=json.dumps(result, default=str),
                )

            elif tool_call.name == "fhir_write":
                manifest_item_id = tool_call.arguments.get(
                    "manifest_item_id"
                )
                if not self._is_item_approved(session, manifest_item_id):
                    return ToolResult(
                        tool_call_id=tool_call.id,
                        content=(
                            "Error: manifest item is not approved. "
                            "Writes require clinician approval first."
                        ),
                        is_error=True,
                    )

                result = await self.openemr_client.fhir_write(
                    resource_type=tool_call.arguments["resource_type"],
                    payload=tool_call.arguments["payload"],
                )
                self._mark_item_executed(session, manifest_item_id)
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=json.dumps(result, default=str),
                )

            elif tool_call.name == "openemr_api":
                result = await self.openemr_client.api_request(
                    endpoint=tool_call.arguments["endpoint"],
                    method=tool_call.arguments["method"],
                    payload=tool_call.arguments.get("payload"),
                )
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=json.dumps(result, default=str),
                )

            elif tool_call.name == "get_page_context":
                ctx = session.page_context
                if ctx is None:
                    return ToolResult(
                        tool_call_id=tool_call.id,
                        content=json.dumps(
                            {"message": "No page context available."}
                        ),
                    )
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=ctx.model_dump_json(),
                )

            elif tool_call.name == "submit_manifest":
                manifest = self._build_manifest(tool_call.arguments, session)
                session.manifest = manifest
                session.phase = "reviewing"
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=json.dumps(
                        {
                            "status": "manifest_submitted",
                            "manifest_id": manifest.id,
                            "item_count": len(manifest.items),
                            "message": (
                                "Change manifest submitted for clinician "
                                "review. Awaiting approval."
                            ),
                        }
                    ),
                )

            else:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    content=f"Error: unknown tool '{tool_call.name}'.",
                    is_error=True,
                )

        except Exception as exc:
            logger.exception("Tool execution failed: %s", tool_call.name)
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Error executing {tool_call.name}: {exc}",
                is_error=True,
            )

    async def execute_approved(self, session: AgentSession) -> AgentSession:
        """Execute all approved manifest items via fhir_write.

        Called after the clinician approves items in the manifest.
        """
        if session.manifest is None:
            raise ValueError("No manifest to execute.")

        session.phase = "executing"
        session.manifest.status = "executing"

        sorted_items = self._topological_sort(session.manifest.items)

        for item in sorted_items:
            if item.status != "approved":
                continue

            try:
                if item.action == ManifestAction.DELETE:
                    result = await self.openemr_client.api_request(
                        endpoint=f"/fhir/{item.resource_type}/{item.proposed_value.get('id', '')}",
                        method="DELETE",
                    )
                else:
                    result = await self.openemr_client.fhir_write(
                        resource_type=item.resource_type,
                        payload=item.proposed_value,
                    )

                item.status = "completed"
                logger.info(
                    "Executed manifest item %s: %s %s",
                    item.id,
                    item.action.value,
                    item.resource_type,
                )
            except Exception as exc:
                item.status = "failed"
                logger.exception(
                    "Failed to execute manifest item %s: %s", item.id, exc
                )
                session.manifest.status = "failed"
                session.phase = "complete"
                return session

        session.manifest.status = "completed"
        session.phase = "complete"

        session.messages.append(
            AgentMessage(
                role="assistant",
                content=(
                    f"All approved changes have been executed successfully. "
                    f"{sum(1 for i in session.manifest.items if i.status == 'completed')} "
                    f"item(s) completed."
                ),
            )
        )

        return session

    def _build_messages(self, session: AgentSession) -> list[dict[str, Any]]:
        """Convert session messages to Anthropic API format."""
        messages: list[dict[str, Any]] = []

        for msg in session.messages:
            if msg.role == "user":
                messages.append({"role": "user", "content": msg.content})

            elif msg.role == "assistant":
                content: list[dict[str, Any]] = []
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        content.append(
                            {
                                "type": "tool_use",
                                "id": tc.id,
                                "name": tc.name,
                                "input": tc.arguments,
                            }
                        )
                messages.append({"role": "assistant", "content": content})

            elif msg.role == "tool":
                if msg.tool_results:
                    content_blocks: list[dict[str, Any]] = []
                    for tr in msg.tool_results:
                        content_blocks.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tr.tool_call_id,
                                "content": tr.content,
                                "is_error": tr.is_error,
                            }
                        )
                    messages.append(
                        {"role": "user", "content": content_blocks}
                    )

        return messages

    def _get_system_prompt(self, session: AgentSession) -> str:
        """Build the system prompt, incorporating page context if available."""
        prompt = SYSTEM_PROMPT

        if session.page_context:
            ctx = session.page_context
            prompt += "\n\n## Current Context\n"
            if ctx.patient_id:
                prompt += f"- Active patient ID: {ctx.patient_id}\n"
            if ctx.encounter_id:
                prompt += f"- Active encounter ID: {ctx.encounter_id}\n"
            if ctx.page_type:
                prompt += f"- Current page: {ctx.page_type}\n"

        if session.phase == "reviewing" and session.manifest:
            prompt += (
                "\n\n## Active Manifest\n"
                f"Manifest {session.manifest.id} is under review with "
                f"{len(session.manifest.items)} item(s). "
                "Wait for clinician approval before executing writes."
            )

        return prompt

    def _extract_tool_calls(
        self, response: anthropic.types.Message
    ) -> list[ToolCall]:
        """Extract tool calls from an Anthropic response."""
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input,
                    )
                )
        return tool_calls

    def _extract_text(self, response: anthropic.types.Message) -> str:
        """Extract text content from an Anthropic response."""
        parts: list[str] = []
        for block in response.content:
            if block.type == "text":
                parts.append(block.text)
        return "\n".join(parts)

    def _build_manifest(
        self, arguments: dict[str, Any], session: AgentSession
    ) -> ChangeManifest:
        """Build a ChangeManifest from submit_manifest tool arguments."""
        items: list[ManifestItem] = []
        for raw_item in arguments.get("items", []):
            items.append(
                ManifestItem(
                    resource_type=raw_item["resource_type"],
                    action=ManifestAction(raw_item["action"]),
                    proposed_value=raw_item["proposed_value"],
                    current_value=raw_item.get("current_value"),
                    source_reference=raw_item["source_reference"],
                    description=raw_item["description"],
                    confidence=raw_item.get("confidence", "high"),
                    depends_on=raw_item.get("depends_on", []),
                )
            )

        return ChangeManifest(
            patient_id=arguments["patient_id"],
            encounter_id=arguments.get("encounter_id")
            or (
                session.page_context.encounter_id
                if session.page_context
                else None
            ),
            items=items,
        )

    def _is_item_approved(
        self, session: AgentSession, manifest_item_id: str | None
    ) -> bool:
        """Check if a manifest item has been approved for execution."""
        if not manifest_item_id or not session.manifest:
            return False
        for item in session.manifest.items:
            if item.id == manifest_item_id and item.status == "approved":
                return True
        return False

    def _mark_item_executed(
        self, session: AgentSession, manifest_item_id: str | None
    ) -> None:
        """Mark a manifest item as completed after successful write."""
        if not manifest_item_id or not session.manifest:
            return
        for item in session.manifest.items:
            if item.id == manifest_item_id:
                item.status = "completed"
                break

    def _topological_sort(self, items: list[ManifestItem]) -> list[ManifestItem]:
        """Sort manifest items respecting depends_on ordering."""
        item_map = {item.id: item for item in items}
        visited: set[str] = set()
        result: list[ManifestItem] = []

        def visit(item_id: str) -> None:
            if item_id in visited:
                return
            visited.add(item_id)
            item = item_map.get(item_id)
            if item is None:
                return
            for dep_id in item.depends_on:
                visit(dep_id)
            result.append(item)

        for item in items:
            visit(item.id)

        return result
