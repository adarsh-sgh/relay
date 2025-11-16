"""Durable-execution tests against Temporal's time-skipping test server.

Covers: full pipeline with human-in-the-loop approval, activity failure
injection (retries), and durability across a worker restart mid-workflow.
"""

import asyncio
import uuid

import pytest
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from relay.demo import DemoLLM
from relay.workflow import TASK_QUEUE, AgentActivities, AgentPipeline, PipelineInput


class FlakyLLM:
    """Fails the first `failures` calls, then delegates to DemoLLM."""

    def __init__(self, failures: int):
        self.remaining = failures
        self.total_failures = 0
        self._inner = DemoLLM()

    def complete(self, messages):
        if self.remaining > 0:
            self.remaining -= 1
            self.total_failures += 1
            raise RuntimeError("injected LLM outage")
        return self._inner.complete(messages)


@pytest.fixture
async def env():
    env = await WorkflowEnvironment.start_time_skipping()
    yield env
    await env.shutdown()


def make_worker(client: Client, llm) -> Worker:
    acts = AgentActivities(llm)
    return Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[AgentPipeline],
        activities=[acts.plan, acts.execute_step, acts.synthesize],
        # no sticky queue: lets a fresh worker pick up a workflow left behind
        # by a dead one immediately (see worker-restart test)
        max_cached_workflows=0,
    )


async def start_and_wait_for_approval(client: Client):
    handle = await client.start_workflow(
        AgentPipeline.run,
        PipelineInput(task="compute 6*7 and report"),
        id=f"wf-{uuid.uuid4().hex[:8]}",
        task_queue=TASK_QUEUE,
    )
    for _ in range(100):
        if await handle.query(AgentPipeline.status) == "awaiting_approval":
            return handle
        await asyncio.sleep(0.1)
    raise AssertionError("workflow never reached awaiting_approval")


async def test_pipeline_with_hitl_approval(env: WorkflowEnvironment):
    async with make_worker(env.client, DemoLLM()):
        handle = await start_and_wait_for_approval(env.client)
        await handle.signal(AgentPipeline.approve)
        result = await handle.result()

    assert result["status"] == "completed"
    assert result["plan"]["steps"] == ["calc: 6*7", "note: verified arithmetic"]
    assert result["results"][0]["output"] == "42"
    assert "42" in result["answer"]["answer"]


async def test_reject_stops_pipeline(env: WorkflowEnvironment):
    async with make_worker(env.client, DemoLLM()):
        handle = await start_and_wait_for_approval(env.client)
        await handle.signal(AgentPipeline.reject)
        result = await handle.result()

    assert result["status"] == "rejected"
    assert "answer" not in result


async def test_activity_failure_injection_is_retried(env: WorkflowEnvironment):
    flaky = FlakyLLM(failures=2)  # plan activity fails twice, retry policy covers it
    async with make_worker(env.client, flaky):
        handle = await start_and_wait_for_approval(env.client)
        await handle.signal(AgentPipeline.approve)
        result = await handle.result()

    assert flaky.total_failures == 2
    assert result["status"] == "completed"
    assert result["results"][0]["output"] == "42"


async def test_workflow_survives_worker_restart(env: WorkflowEnvironment):
    # worker 1 plans, then dies while the workflow is paused for approval
    async with make_worker(env.client, DemoLLM()):
        handle = await start_and_wait_for_approval(env.client)

    # no worker alive; workflow state lives in the server
    await handle.signal(AgentPipeline.approve)

    # a fresh worker process picks the workflow back up and finishes it
    async with make_worker(env.client, DemoLLM()):
        result = await handle.result()

    assert result["status"] == "completed"
    assert result["results"][0]["output"] == "42"
