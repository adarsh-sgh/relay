"""Operator console end to end, over the ASGI app against the Temporal test server.

An operator lists templates, starts a run with bad then good params, sees
it in the approval queue, decides with a name and reason, and reads the
audit trail; a no-approval template runs straight through.
"""

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from relay.console import create_app
from relay.demo import DemoLLM
from relay.templates import TemplateRegistry
from relay.workflow import TASK_QUEUE, AgentActivities, AgentPipeline

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


@pytest.fixture
async def env():
    env = await WorkflowEnvironment.start_time_skipping()
    yield env
    await env.shutdown()


async def wait_status(api: AsyncClient, workflow_id: str, wanted: set[str]) -> dict:
    for _ in range(100):
        run = (await api.get(f"/runs/{workflow_id}")).json()
        if run["status"] in wanted:
            return run
        await asyncio.sleep(0.1)
    raise AssertionError(f"{workflow_id} never reached {wanted}, last: {run['status']}")


async def test_operator_flow_start_queue_decide_audit(env: WorkflowEnvironment):
    acts = AgentActivities(DemoLLM())
    worker = Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[AgentPipeline],
        activities=[acts.plan, acts.execute_step, acts.synthesize],
    )
    app = create_app(env.client, TemplateRegistry.load(TEMPLATES_DIR))
    async with worker, AsyncClient(transport=ASGITransport(app=app), base_url="http://console") as api:
        templates = (await api.get("/templates")).json()
        arith = next(t for t in templates if t["name"] == "arithmetic-check")
        assert arith["owner"] and arith["slo"]["success_rate"] == 0.99
        assert [p["name"] for p in arith["params"]] == ["expression"]

        # operator mistakes are 4xx, not 500s
        assert (await api.post("/runs", json={"template": "nope"})).status_code == 404
        bad = await api.post("/runs", json={"template": "arithmetic-check", "params": {}})
        assert bad.status_code == 400 and "missing params: expression" in bad.json()["detail"]
        assert (await api.get("/runs/relay-missing")).status_code == 404

        started = await api.post(
            "/runs",
            json={
                "template": "arithmetic-check",
                "params": {"expression": "6*7"},
                "requested_by": "sales-ops@example.com",
            },
        )
        assert started.status_code == 201
        wf = started.json()["workflow_id"]
        assert wf.startswith("relay-arithmetic-check-")

        run = await wait_status(api, wf, {"awaiting_approval"})
        assert run["template"] == "arithmetic-check" and run["requested_by"] == "sales-ops@example.com"

        queue = (await api.get("/approvals")).json()
        assert [q["workflow_id"] for q in queue] == [wf]
        assert queue[0]["params"] == {"expression": "6*7"} and queue[0]["waiting_since"]

        decided = await api.post(
            f"/runs/{wf}/decision",
            json={"verdict": "approve", "actor": "lead@example.com", "reason": "plan matches request"},
        )
        assert decided.status_code == 200
        run = await wait_status(api, wf, {"completed", "failed", "rejected"})
        assert run["status"] == "completed"
        decision = next(e for e in run["audit"] if e["event"] == "decision")
        assert decision["actor"] == "lead@example.com" and decision["reason"] == "plan matches request"

        # deciding twice is a conflict, and the finished run leaves the queue
        again = await api.post(f"/runs/{wf}/decision", json={"verdict": "reject", "actor": "x"})
        assert again.status_code == 409
        assert (await api.get("/approvals")).json() == []

        # template without approval runs straight through with its defaults
        auto = (await api.post("/runs", json={"template": "daily-digest", "params": {"team": "ops"}})).json()
        run = await wait_status(api, auto["workflow_id"], {"completed", "failed"})
        assert run["status"] == "completed"
        assert "awaiting_approval" not in [e["event"] for e in run["audit"]]
        assert run["params"] == {"team": "ops"}
