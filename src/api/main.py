from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from ..agent.loop import AgentLoop
from ..agent.models import AgentSession, PageContext
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

# In-memory session store
_sessions: dict[str, AgentSession] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    base_url = os.environ.get("OPENEMR_BASE_URL", "http://localhost:80")
    fhir_url = os.environ.get(
        "OPENEMR_FHIR_URL", "http://localhost:80/apis/default/fhir"
    )
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    openemr_client = OpenEMRClient(base_url=base_url, fhir_url=fhir_url)
    tool_registry = ToolRegistry(openemr_client)
    register_default_tools(tool_registry)

    anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)
    agent_loop = AgentLoop(
        anthropic_client=anthropic_client,
        openemr_client=openemr_client,
        tools_registry=tool_registry,
    )

    app.state.openemr_client = openemr_client
    app.state.tool_registry = tool_registry
    app.state.agent_loop = agent_loop

    yield

    await openemr_client.close()


tracer = setup_tracing("openemr-agent")

app = FastAPI(title="OpenEMR Clinical Agent", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FastAPIInstrumentor.instrument_app(app)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _get_or_create_session(session_id: str | None) -> AgentSession:
    if session_id and session_id in _sessions:
        return _sessions[session_id]
    session = AgentSession()
    _sessions[session.id] = session
    return session


def _get_session(session_id: str) -> AgentSession:
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    session = _get_or_create_session(req.session_id)

    if req.page_context:
        session.page_context = PageContext(
            patient_id=req.page_context.patient_id,
            encounter_id=req.page_context.encounter_id,
            page_type=req.page_context.page_type,
        )

    agent_loop: AgentLoop = app.state.agent_loop
    session = await agent_loop.run(session, req.message)
    _sessions[session.id] = session

    last_assistant = ""
    for msg in reversed(session.messages):
        if msg.role == "assistant" and msg.content:
            last_assistant = msg.content
            break

    return ChatResponse(
        session_id=session.id,
        response=last_assistant,
        manifest=session.manifest.model_dump() if session.manifest else None,
        phase=session.phase,
    )


@app.post("/api/manifest/{session_id}/approve", response_model=ApprovalResponse)
async def approve_manifest(session_id: str, req: ApprovalRequest) -> ApprovalResponse:
    session = _get_session(session_id)

    if session.manifest is None:
        raise HTTPException(status_code=400, detail="No manifest for this session")

    for item in session.manifest.items:
        if item.id in req.approved_items:
            item.status = "approved"
        elif item.id in req.rejected_items:
            item.status = "rejected"

    openemr_client: OpenEMRClient = app.state.openemr_client

    has_approved = any(item.status == "approved" for item in session.manifest.items)
    if has_approved:
        report = await verify_manifest(session.manifest, openemr_client)
    else:
        from ..verification.checks import VerificationReport

        report = VerificationReport(manifest_id=session.manifest.id)

    return ApprovalResponse(
        session_id=session.id,
        manifest_id=session.manifest.id,
        results=[r.model_dump() for r in report.results],
        passed=report.passed,
    )


@app.post("/api/manifest/{session_id}/execute")
async def execute_manifest(session_id: str) -> dict[str, Any]:
    session = _get_session(session_id)

    if session.manifest is None:
        raise HTTPException(status_code=400, detail="No manifest for this session")

    agent_loop: AgentLoop = app.state.agent_loop
    session = await agent_loop.execute_approved(session)
    _sessions[session.id] = session

    return {
        "session_id": session.id,
        "phase": session.phase,
        "manifest_status": session.manifest.status if session.manifest else None,
        "items": [
            {"id": item.id, "status": item.status}
            for item in (session.manifest.items if session.manifest else [])
        ],
    }


@app.get("/api/manifest/{session_id}", response_model=ManifestResponse)
async def get_manifest(session_id: str) -> ManifestResponse:
    session = _get_session(session_id)
    return ManifestResponse(
        session_id=session.id,
        manifest=session.manifest.model_dump() if session.manifest else None,
    )


@app.get("/api/sessions")
async def list_sessions() -> list[dict[str, Any]]:
    return [
        {"session_id": s.id, "phase": s.phase, "message_count": len(s.messages)}
        for s in _sessions.values()
    ]


@app.get("/api/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    connected = False
    try:
        openemr_client: OpenEMRClient = app.state.openemr_client
        metadata = await openemr_client.get_fhir_metadata()
        connected = "error" not in metadata
    except Exception:
        pass
    return HealthResponse(status="healthy", openemr_connected=connected)


@app.get("/api/fhir/metadata")
async def fhir_metadata() -> dict[str, Any]:
    openemr_client: OpenEMRClient = app.state.openemr_client
    return await openemr_client.get_fhir_metadata()
