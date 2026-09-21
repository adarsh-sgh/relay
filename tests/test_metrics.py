"""Audit trail and SLO metrics, end to end.

One completed run (approved by a named actor) and one that exhausts its
retries; then scrape the worker's Prometheus endpoint and check the two
series behind docs/handoff.md, plus the audit trail each run leaves.
"""

import asyncio
import re
import urllib.request
import uuid

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from relay.demo import DemoLLM
from relay.metrics import prometheus_runtime
from relay.workflow import TASK_QUEUE, AgentActivities, AgentPipeline, Decision, PipelineInput

METRICS_ADDR = "127.0.0.1:9798"


class FlakyLLM:
    def __init__(self, failures: int):
        self.remaining = failures
        self._inner = DemoLLM()

    def complete(self, messages):
        if self.remaining > 0:
            self.remaining -= 1
            raise RuntimeError("injected LLM outage")
        return self._inner.complete(messages)


@pytest.fixture
async def env():
    env = await WorkflowEnvironment.start_time_skipping(runtime=prometheus_runtime(METRICS_ADDR))
    yield env
    await env.shutdown()


def scrape() -> dict[str, float]:
    body = urllib.request.urlopen(f"http://{METRICS_ADDR}/metrics").read().decode()
    out = {}
    for line in body.splitlines():
        if line.startswith("relay_"):
            name, value = line.rsplit(" ", 1)
            out[name] = float(value)
    return out


def series(metrics: dict[str, float], name: str, **labels: str) -> float:
    """Sum of samples of `name` whose label set contains all `labels`."""
    total = 0.0
    for key, value in metrics.items():
        if not key.startswith(name + "{"):
            continue
        if all(f'{k}="{v}"' in key for k, v in labels.items()):
            total += value
    return total


async def wait_for(handle, status: str):
    for _ in range(100):
        if await handle.query(AgentPipeline.status) == status:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"never reached {status}")


async def test_audit_trail_and_slo_metrics(env: WorkflowEnvironment):
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[AgentPipeline],
        activities=[a for a in [AgentActivities(DemoLLM())] for a in (a.plan, a.execute_step, a.synthesize)],
    ):
        ok = await env.client.start_workflow(
            AgentPipeline.run,
            PipelineInput(
                task="compute 6*7 and report",
                template="arithmetic-check",
                requested_by="sales-ops@example.com",
                params={"expression": "6*7"},
            ),
            id=f"wf-{uuid.uuid4().hex[:8]}",
            task_queue=TASK_QUEUE,
        )
        await wait_for(ok, "awaiting_approval")
        assert await ok.query(AgentPipeline.meta) == {
            "template": "arithmetic-check",
            "requested_by": "sales-ops@example.com",
            "params": {"expression": "6*7"},
        }
        await ok.signal(
            AgentPipeline.decide, Decision(verdict="approve", actor="lead@example.com", reason="looks right")
        )
        result = await ok.result()
        # audit stays queryable after the run closes
        closed_audit = await ok.query(AgentPipeline.audit)

    events = [e["event"] for e in result["audit"]]
    assert events == [
        "started", "planning", "awaiting_approval", "decision", "executing", "synthesizing", "completed",
    ]
    decision = next(e for e in result["audit"] if e["event"] == "decision")
    assert decision["actor"] == "lead@example.com" and decision["reason"] == "looks right"
    assert all(re.match(r"\d{4}-\d{2}-\d{2}T", e["at"]) for e in result["audit"])
    assert closed_audit == result["audit"]

    # a run whose plan step fails more times than the retry policy allows
    flaky = AgentActivities(FlakyLLM(failures=10))
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[AgentPipeline],
        activities=[flaky.plan, flaky.execute_step, flaky.synthesize],
    ):
        bad = await env.client.start_workflow(
            AgentPipeline.run,
            PipelineInput(task="compute 1+1", require_approval=False, template="arithmetic-check"),
            id=f"wf-{uuid.uuid4().hex[:8]}",
            task_queue=TASK_QUEUE,
        )
        with pytest.raises(WorkflowFailureError):
            await bad.result()
        audit = await bad.query(AgentPipeline.audit)
    assert audit[-1]["event"] == "failed" and "injected LLM outage" in audit[-1]["error"]

    await asyncio.sleep(0.3)  # exporter flush
    m = scrape()
    assert series(m, "relay_runs", template="arithmetic-check", status="completed") == 1
    assert series(m, "relay_runs", template="arithmetic-check", status="failed") == 1
    # success rate = completed / all finished, the SLO numerator/denominator
    assert series(m, "relay_runs", template="arithmetic-check") == 2
    assert series(m, "relay_step_latency_count", activity="plan", outcome="ok") == 1
    assert series(m, "relay_step_latency_count", activity="plan", outcome="error") == 4  # max attempts
    assert series(m, "relay_step_latency_count", activity="execute_step", outcome="ok") == 2
    assert series(m, "relay_step_latency_bucket", activity="plan", le="+Inf") == 5
