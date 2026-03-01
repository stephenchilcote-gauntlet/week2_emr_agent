from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class PageContextRequest(BaseModel):
    patient_id: str | None = None
    encounter_id: str | None = None
    page_type: str | None = None
    visible_data: dict[str, Any] | None = None

    @field_validator("patient_id", "encounter_id", mode="before")
    @classmethod
    def coerce_to_str(cls, v: Any) -> str | None:
        """Accept integer IDs from older embed.js versions that poll Knockout."""
        if v is None:
            return None
        return str(v)


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    page_context: PageContextRequest | None = None


class ChatResponse(BaseModel):
    session_id: str
    response: str
    manifest: dict[str, Any] | None = None
    phase: str
    error: str | None = None
    tool_calls_summary: list[dict[str, Any]] | None = None
    openemr_pid: str | None = None
    navigate_to_patient: dict[str, Any] | None = None


class ApprovalRequest(BaseModel):
    approved_items: list[str] = Field(default_factory=list)
    rejected_items: list[str] = Field(default_factory=list)
    modified_items: list[dict[str, Any]] = Field(default_factory=list)


class ApprovalResponse(BaseModel):
    session_id: str
    manifest_id: str
    results: list[dict[str, Any]] = Field(default_factory=list)
    passed: bool


class ManifestResponse(BaseModel):
    session_id: str
    manifest: dict[str, Any] | None = None


class FeedbackRequest(BaseModel):
    message_index: int
    rating: str  # "up" or "down"


class HealthResponse(BaseModel):
    status: str
    openemr_connected: bool
    openemr_status: str
