"""Operator console: HTTP API for people who trigger and approve runs.

Start a run by template name and parameters, watch its status and audit
trail, work an approval queue, and record who decided what and why. The
workflow is the source of truth; this service only proxies Temporal and
keeps an index of run ids so it can find runs still waiting on a person.

    python -m relay.console   # :8080, needs temporal + relay.worker
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Literal, Protocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from relay.templates import TemplateNotFound, TemplateParamError, TemplateRegistry
from relay.workflow import TASK_QUEUE, AgentPipeline, Decision, PipelineInput

TERMINAL = {"completed", "rejected", "failed"}


class RunIndex(Protocol):
    """Where the console finds runs that may still need a decision."""

    def add(self, workflow_id: str) -> None: ...
    def discard(self, workflow_id: str) -> None: ...
    async def candidates(self) -> list[str]: ...


class MemoryRunIndex:
    """Runs started by this process. Lost on restart; fine for tests and
    single-instance dev, use VisibilityRunIndex against a real server."""

    def __init__(self) -> None:
        self._ids: set[str] = set()

    def add(self, workflow_id: str) -> None:
        self._ids.add(workflow_id)

    def discard(self, workflow_id: str) -> None:
        self._ids.discard(workflow_id)

    async def candidates(self) -> list[str]:
        return sorted(self._ids)


class VisibilityRunIndex:
    """Open AgentPipeline runs from Temporal visibility, so the queue survives
    console restarts and covers runs started elsewhere (gRPC, CLI). Needs a
    real server: the time-skipping test server does not implement list."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def add(self, workflow_id: str) -> None:
        pass

    def discard(self, workflow_id: str) -> None:
        pass

    async def candidates(self) -> list[str]:
        query = "WorkflowType='AgentPipeline' AND ExecutionStatus='Running'"
        return [w.id async for w in self._client.list_workflows(query)]


class StartRunRequest(BaseModel):
    template: str
    params: dict[str, str] = {}
    requested_by: str = ""


class DecisionRequest(BaseModel):
    verdict: Literal["approve", "reject"]
    actor: str
    reason: str = ""


def create_app(client: Client, registry: TemplateRegistry, index: RunIndex | None = None) -> FastAPI:
    index = index if index is not None else MemoryRunIndex()
    app = FastAPI(title="relay console")

    def handle(workflow_id: str):
        return client.get_workflow_handle_for(AgentPipeline.run, workflow_id)

    async def query(workflow_id: str, q):
        try:
            return await handle(workflow_id).query(q)
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                raise HTTPException(404, f"run {workflow_id} not found") from exc
            raise

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    @app.get("/templates")
    async def list_templates() -> list[dict]:
        return [t.model_dump() for t in registry.list()]

    @app.post("/runs", status_code=201)
    async def start_run(req: StartRunRequest) -> dict:
        try:
            template = registry.get(req.template)
            task = template.render(req.params)
        except TemplateNotFound:
            raise HTTPException(404, f"template {req.template!r} not found")
        except TemplateParamError as exc:
            raise HTTPException(400, str(exc))
        workflow_id = f"relay-{template.name}-{uuid.uuid4().hex[:8]}"
        await client.start_workflow(
            AgentPipeline.run,
            PipelineInput(
                task=task,
                require_approval=template.require_approval,
                template=template.name,
                requested_by=req.requested_by,
                params=req.params,
            ),
            id=workflow_id,
            task_queue=TASK_QUEUE,
        )
        index.add(workflow_id)
        return {"workflow_id": workflow_id, "status": "started", "owner": template.owner}

    @app.get("/runs/{workflow_id}")
    async def get_run(workflow_id: str) -> dict:
        status = await query(workflow_id, AgentPipeline.status)
        meta = await query(workflow_id, AgentPipeline.meta)
        audit = await query(workflow_id, AgentPipeline.audit)
        return {"workflow_id": workflow_id, "status": status, **meta, "audit": audit}

    @app.get("/approvals")
    async def pending_approvals() -> list[dict]:
        pending = []
        for workflow_id in await index.candidates():
            status = await query(workflow_id, AgentPipeline.status)
            if status in TERMINAL:
                index.discard(workflow_id)
                continue
            if status != "awaiting_approval":
                continue
            meta = await query(workflow_id, AgentPipeline.meta)
            audit = await query(workflow_id, AgentPipeline.audit)
            pending.append({"workflow_id": workflow_id, "waiting_since": audit[-1]["at"], **meta})
        return pending

    @app.post("/runs/{workflow_id}/decision")
    async def decide(workflow_id: str, req: DecisionRequest) -> dict:
        status = await query(workflow_id, AgentPipeline.status)
        if status != "awaiting_approval":
            raise HTTPException(409, f"run is {status}, not awaiting_approval")
        await handle(workflow_id).signal(
            AgentPipeline.decide, Decision(verdict=req.verdict, actor=req.actor, reason=req.reason)
        )
        return {"workflow_id": workflow_id, "verdict": req.verdict, "actor": req.actor}

    return app


async def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    client = await Client.connect(os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"))
    registry = TemplateRegistry.load(os.environ.get("RELAY_TEMPLATES", "templates"))
    app = create_app(client, registry, VisibilityRunIndex(client))
    port = int(os.environ.get("RELAY_CONSOLE_PORT", "8080"))
    logging.info("relay console on :%d with %d templates", port, len(registry.list()))
    await uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")).serve()


if __name__ == "__main__":
    asyncio.run(main())
