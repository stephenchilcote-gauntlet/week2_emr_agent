from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any]
    id: str


class ToolResult(BaseModel):
    tool_call_id: str
    content: str
    is_error: bool = False


class ManifestAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class ManifestItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    resource_type: str
    action: ManifestAction
    proposed_value: dict[str, Any]
    current_value: dict[str, Any] | None = None
    source_reference: str
    description: str
    confidence: str = "high"
    status: str = "pending"
    depends_on: list[str] = Field(default_factory=list)


class ChangeManifest(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    patient_id: str
    encounter_id: str | None = None
    items: list[ManifestItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = "draft"


class PageContext(BaseModel):
    patient_id: str | None = None
    encounter_id: str | None = None
    page_type: str | None = None
    active_form: dict[str, Any] | None = None


class AgentMessage(BaseModel):
    role: str
    content: str
    tool_calls: list[ToolCall] | None = None
    tool_results: list[ToolResult] | None = None


class AgentSession(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    messages: list[AgentMessage] = Field(default_factory=list)
    manifest: ChangeManifest | None = None
    page_context: PageContext | None = None
    phase: str = "planning"
