from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from collections import Counter

import anthropic
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from opentelemetry import trace as otel_trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from ..agent.loop import AgentLoop
from ..agent.models import AgentSession, PageContext
from ..observability.audit import AuditEvent, AuditStore
from ..observability.tracing import setup_tracing
from ..tools.openemr_client import OpenEMRClient
from ..tools.registry import ToolRegistry, register_default_tools
from ..verification.checks import verify_manifest
from .schemas import (
    ApprovalRequest,
    ApprovalResponse,
    ChatRequest,
    ChatResponse,
    HealthResponse,
    ManifestResponse,
)
from .session_store import SessionStore

_session_locks: dict[str, asyncio.Lock] = {}
_ephemeral_sessions: dict[str, AgentSession] = {}
_web_root = Path(__file__).resolve().parents[2] / "web" / "sidebar"


@asynccontextmanager
async def lifespan(app: FastAPI):
    base_url = os.environ.get("OPENEMR_BASE_URL", "http://localhost:80")
    fhir_url = os.environ.get(
        "OPENEMR_FHIR_URL", "http://localhost:80/apis/default/fhir"
    )
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    openemr_client = OpenEMRClient(
        base_url=base_url,
        fhir_url=fhir_url,
        client_id=os.environ.get("OPENEMR_CLIENT_ID", ""),
        client_secret=os.environ.get("OPENEMR_CLIENT_SECRET", ""),
        username=os.environ.get("OPENEMR_USER", "admin"),
        password=os.environ.get("OPENEMR_PASS", "pass"),
    )
    tool_registry = ToolRegistry(openemr_client)
    register_default_tools(tool_registry)

    anthropic_client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=5)
    agent_loop = AgentLoop(
        anthropic_client=anthropic_client,
        openemr_client=openemr_client,
        tools_registry=tool_registry,
        tracer=tracer,
    )
    session_store = SessionStore(os.environ.get("SESSION_DB_PATH", "data/sessions.db"))
    audit_store = AuditStore(os.environ.get("AUDIT_DB_PATH", "data/audit.db"))

    app.state.openemr_client = openemr_client
    app.state.tool_registry = tool_registry
    app.state.agent_loop = agent_loop
    app.state.session_store = session_store
    app.state.audit_store = audit_store

    yield

    await openemr_client.close()


tracer = setup_tracing("openemr-agent")

app = FastAPI(title="OpenEMR Clinical Agent", version="0.1.0", lifespan=lifespan)

cors_origins = [
    origin.strip()
    for origin in os.environ.get("CORS_ALLOW_ORIGINS", "*").split(",")
    if origin.strip()
]
allow_credentials = False if cors_origins == ["*"] else True

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

FastAPIInstrumentor.instrument_app(app)


def _require_user_id(
    openemr_user_id: str | None = Header(default=None, alias="openemr_user_id"),
) -> str:
    if not openemr_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return openemr_user_id


def _summarize_tool_calls(session: AgentSession) -> list[dict[str, Any]] | None:
    all_counts: Counter = Counter()
    for message in session.messages:
        if message.role == "assistant" and message.tool_calls:
            all_counts.update(tc.name for tc in message.tool_calls)
    if not all_counts:
        return None
    return [
        {"name": tool_name, "count": count}
        for tool_name, count in sorted(all_counts.items())
    ]


def _resolve_session(
    session_id: str,
    user_id: str,
    session_store: SessionStore,
) -> AgentSession | None:
    ephemeral = _ephemeral_sessions.get(session_id)
    if ephemeral is not None:
        return ephemeral if ephemeral.openemr_user_id == user_id else None
    return session_store.load(session_id, user_id)


def _get_or_create_session(
    session_id: str | None,
    user_id: str,
    session_store: SessionStore,
) -> AgentSession:
    if session_id is None:
        session = AgentSession(openemr_user_id=user_id)
        _ephemeral_sessions[session.id] = session
        return session

    session = _resolve_session(session_id, user_id, session_store)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def _get_session(
    session_id: str,
    user_id: str,
    session_store: SessionStore,
) -> AgentSession:
    session = _resolve_session(session_id, user_id, session_store)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def _session_summary(session: AgentSession) -> dict[str, Any]:
    first_user = next((m for m in session.messages if m.role == "user"), None)
    preview = (first_user.content if first_user else "")[:60]
    patient_name = None
    if session.page_context and session.page_context.visible_data:
        visible_data = session.page_context.visible_data
        if isinstance(visible_data, dict):
            patient_name = visible_data.get("patient_name") or visible_data.get("name")
    return {
        "session_id": session.id,
        "phase": session.phase,
        "message_count": len(session.messages),
        "created_at": session.created_at.isoformat(),
        "first_message_preview": preview,
        "patient_name": patient_name,
        "patient_id": session.page_context.patient_id if session.page_context else None,
    }


if _web_root.exists():
    app.mount(
        "/ui/assets",
        StaticFiles(directory=str(_web_root)),
        name="sidebar-assets",
    )


@app.get("/ui")
async def sidebar_ui() -> FileResponse:
    if not _web_root.exists():
        raise HTTPException(status_code=404, detail="Sidebar UI not available")
    index_path = _web_root / "index.html"
    return FileResponse(index_path)


@app.post("/api/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    user_id: str = Depends(_require_user_id),
) -> ChatResponse:
    session_store: SessionStore = app.state.session_store
    session = _get_or_create_session(req.session_id, user_id, session_store)
    otel_trace.get_current_span().set_attribute("session.id", session.id)

    audit_store: AuditStore = app.state.audit_store
    audit_store.record(AuditEvent(
        session_id=session.id,
        user_id=user_id,
        event_type="chat_received",
        summary=f"User message received ({len(req.message)} chars)",
        details={"message_length": len(req.message)},
    ))

    if req.page_context:
        session.page_context = PageContext(
            patient_id=req.page_context.patient_id,
            encounter_id=req.page_context.encounter_id,
            page_type=req.page_context.page_type,
            visible_data=req.page_context.visible_data,
        )

    if session.page_context and session.page_context.patient_id and not session.fhir_patient_id:
        patient_result = await app.state.openemr_client.fhir_read(
            "Patient",
            {"identifier": session.page_context.patient_id},
        )
        if patient_result.get("entry"):
            fhir_id = patient_result["entry"][0].get("resource", {}).get("id")
            if fhir_id:
                session.fhir_patient_id = fhir_id

        # Fallback: look up by patient name (for patients without a numeric identifier,
        # e.g. those inserted directly into the DB bypassing OpenEMR's creation workflow).
        if not session.fhir_patient_id:
            patient_name = (
                (session.page_context.visible_data or {}).get("patient_name") or ""
            )
            if patient_name:
                # Use the last token as the family name for the FHIR name search.
                family = patient_name.strip().split()[-1]
                name_result = await app.state.openemr_client.fhir_read(
                    "Patient",
                    {"name": family, "_count": "5"},
                )
                if name_result.get("entry"):
                    fhir_id = name_result["entry"][0].get("resource", {}).get("id")
                    if fhir_id:
                        session.fhir_patient_id = fhir_id

    agent_loop: AgentLoop = app.state.agent_loop
    try:
        session = await agent_loop.run(session, req.message)
    except anthropic.APIStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"LLM API error: {exc.status_code} {exc.message}",
        ) from exc
    session_store.save(session)
    _ephemeral_sessions.pop(session.id, None)

    last_assistant = ""
    for msg in reversed(session.messages):
        if msg.role == "assistant" and msg.content:
            last_assistant = msg.content
            break

    audit_store.record(AuditEvent(
        session_id=session.id,
        user_id=user_id,
        event_type="assistant_responded",
        summary=f"Assistant responded ({len(last_assistant)} chars)",
        details={"response_length": len(last_assistant)},
    ))

    return ChatResponse(
        session_id=session.id,
        response=last_assistant,
        manifest=session.manifest.model_dump() if session.manifest else None,
        phase=session.phase,
        tool_calls_summary=_summarize_tool_calls(session),
        openemr_pid=session.openemr_pid,
    )


@app.post("/api/sessions")
async def create_session(
    user_id: str = Depends(_require_user_id),
) -> dict[str, str]:
    session = AgentSession(openemr_user_id=user_id)
    _ephemeral_sessions[session.id] = session
    otel_trace.get_current_span().set_attribute("session.id", session.id)
    return {"session_id": session.id, "phase": session.phase}


@app.get("/api/sessions")
async def list_sessions(
    patient_id: str | None = None,
    user_id: str = Depends(_require_user_id),
) -> list[dict[str, Any]]:
    sessions = app.state.session_store.list_for_user(user_id, patient_id=patient_id)
    return [_session_summary(session) for session in sessions]


@app.delete("/api/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    user_id: str = Depends(_require_user_id),
) -> None:
    session_store: SessionStore = app.state.session_store
    _get_session(session_id, user_id, session_store)
    session_store.delete(session_id, user_id)
    _ephemeral_sessions.pop(session_id, None)


@app.get("/api/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
    user_id: str = Depends(_require_user_id),
) -> dict[str, Any]:
    session = _get_session(session_id, user_id, app.state.session_store)
    otel_trace.get_current_span().set_attribute("session.id", session.id)
    return {
        "session_id": session.id,
        "messages": [message.model_dump() for message in session.messages],
        "manifest": session.manifest.model_dump() if session.manifest else None,
    }


@app.post("/api/manifest/{session_id}/approve", response_model=ApprovalResponse)
async def approve_manifest(
    session_id: str,
    req: ApprovalRequest,
    user_id: str = Depends(_require_user_id),
) -> ApprovalResponse:
    session_store: SessionStore = app.state.session_store
    session = _get_session(session_id, user_id, session_store)
    otel_trace.get_current_span().set_attribute("session.id", session.id)

    if session.manifest is None:
        raise HTTPException(status_code=400, detail="No manifest for this session")

    modifications = {
        item.get("id"): item.get("proposed_value")
        for item in req.modified_items
        if item.get("id") and isinstance(item.get("proposed_value"), dict)
    }
    for item in session.manifest.items:
        if item.id in modifications:
            item.proposed_value = modifications[item.id]

    for item in session.manifest.items:
        if item.id in req.approved_items:
            item.status = "approved"
        elif item.id in req.rejected_items:
            item.status = "rejected"
        else:
            item.status = "pending"

    approved_count = sum(1 for item in session.manifest.items if item.status == "approved")
    rejected_count = sum(1 for item in session.manifest.items if item.status == "rejected")
    audit_store: AuditStore = app.state.audit_store
    audit_store.record(AuditEvent(
        session_id=session.id,
        user_id=user_id,
        event_type="manifest_reviewed",
        summary=f"Manifest reviewed: {approved_count} approved, {rejected_count} rejected",
        details={
            "manifest_id": session.manifest.id,
            "approved_count": approved_count,
            "rejected_count": rejected_count,
            "approved_item_ids": req.approved_items,
            "rejected_item_ids": req.rejected_items,
        },
    ))

    openemr_client: OpenEMRClient = app.state.openemr_client

    has_approved = any(item.status == "approved" for item in session.manifest.items)
    if has_approved:
        report = await verify_manifest(session.manifest, openemr_client)
    else:
        from ..verification.checks import VerificationReport

        report = VerificationReport(manifest_id=session.manifest.id)

    session_store.save(session)
    return ApprovalResponse(
        session_id=session.id,
        manifest_id=session.manifest.id,
        results=[r.model_dump() for r in report.results],
        passed=report.passed,
    )


@app.post("/api/manifest/{session_id}/execute")
async def execute_manifest(
    session_id: str,
    user_id: str = Depends(_require_user_id),
) -> dict[str, Any]:
    session_store: SessionStore = app.state.session_store
    session = _get_session(session_id, user_id, session_store)
    otel_trace.get_current_span().set_attribute("session.id", session.id)

    if session.manifest is None:
        raise HTTPException(status_code=400, detail="No manifest for this session")

    lock = _session_locks.setdefault(session_id, asyncio.Lock())
    agent_loop: AgentLoop = app.state.agent_loop
    async with lock:
        session = await agent_loop.execute_approved(session)
    session_store.save(session)

    items = session.manifest.items if session.manifest else []
    completed_count = sum(1 for i in items if i.status == "completed")
    failed_count = sum(1 for i in items if i.status == "failed")
    skipped_count = sum(1 for i in items if i.status in ("rejected", "pending"))
    audit_store: AuditStore = app.state.audit_store
    audit_store.record(AuditEvent(
        session_id=session.id,
        user_id=user_id,
        event_type="manifest_executed",
        summary=f"Manifest executed: {completed_count} completed, {failed_count} failed, {skipped_count} skipped",
        details={
            "manifest_id": session.manifest.id if session.manifest else None,
            "completed_count": completed_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "item_ids": [i.id for i in items],
        },
    ))

    return {
        "session_id": session.id,
        "phase": session.phase,
        "manifest_status": session.manifest.status if session.manifest else None,
        "items": [
            {
                "id": item.id,
                "status": item.status,
                "execution_result": item.execution_result,
            }
            for item in items
        ],
    }


@app.get("/api/sessions/{session_id}/audit")
async def get_session_audit(
    session_id: str,
    user_id: str = Depends(_require_user_id),
) -> list[dict[str, Any]]:
    _get_session(session_id, user_id, app.state.session_store)
    audit_store: AuditStore = app.state.audit_store
    events = audit_store.get_session_events(session_id)
    return [event.model_dump() for event in events]


@app.get("/api/manifest/{session_id}", response_model=ManifestResponse)
async def get_manifest(
    session_id: str,
    user_id: str = Depends(_require_user_id),
) -> ManifestResponse:
    session = _get_session(session_id, user_id, app.state.session_store)
    otel_trace.get_current_span().set_attribute("session.id", session.id)
    return ManifestResponse(
        session_id=session.id,
        manifest=session.manifest.model_dump() if session.manifest else None,
    )


@app.get("/api/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    connected = False
    openemr_status = "starting"
    try:
        openemr_client: OpenEMRClient = app.state.openemr_client
        metadata = await openemr_client.get_fhir_metadata()
        connected = "error" not in metadata
        openemr_status = "ok" if connected else "error"
    except Exception:
        openemr_status = "error"
    return HealthResponse(
        status="healthy",
        openemr_connected=connected,
        openemr_status=openemr_status,
    )


@app.get("/api/fhir/metadata")
async def fhir_metadata() -> dict[str, Any]:
    openemr_client: OpenEMRClient = app.state.openemr_client
    return await openemr_client.get_fhir_metadata()
