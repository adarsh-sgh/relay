"""Durable-execution tests against Temporal's time-skipping test server.

Covers the full pipeline with human-in-the-loop approval and rejection.
"""

import asyncio
import uuid

import pytest
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from relay.demo import DemoLLM
from relay.workflow import TASK_QUEUE, AgentActivities, AgentPipeline, PipelineInput


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
