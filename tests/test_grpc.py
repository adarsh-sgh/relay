"""gRPC control plane end-to-end: StartRun -> GetStatus -> Approve -> completed.

Exercises the same wire contract the Go client (go-client/) uses.
"""

import asyncio

import grpc
import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from relay.demo import DemoLLM
from relay.grpc_server import serve_grpc
from relay.relaypb import relay_pb2, relay_pb2_grpc
from relay.workflow import TASK_QUEUE, AgentActivities, AgentPipeline

PORT = 50971


@pytest.fixture
async def env():
    env = await WorkflowEnvironment.start_time_skipping()
    yield env
    await env.shutdown()


async def test_grpc_start_status_approve_flow(env: WorkflowEnvironment):
    acts = AgentActivities(DemoLLM())
    worker = Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[AgentPipeline],
        activities=[acts.plan, acts.execute_step, acts.synthesize],
    )
    server = await serve_grpc(env.client, port=PORT)
    try:
        async with worker:
            async with grpc.aio.insecure_channel(f"localhost:{PORT}") as channel:
                stub = relay_pb2_grpc.RelayControlStub(channel)

                run = await stub.StartRun(
                    relay_pb2.StartRunRequest(task="compute 6*7 and report", require_approval=True)
                )
                assert run.workflow_id.startswith("relay-")

                for _ in range(100):
                    st = await stub.GetStatus(relay_pb2.StatusRequest(workflow_id=run.workflow_id))
                    if st.status == "awaiting_approval":
                        break
                    await asyncio.sleep(0.1)
                assert st.status == "awaiting_approval"

                ack = await stub.Approve(relay_pb2.ApproveRequest(workflow_id=run.workflow_id))
                assert ack.status == "approved"

                handle = env.client.get_workflow_handle_for(AgentPipeline.run, run.workflow_id)
                result = await handle.result()
                assert result["status"] == "completed"
                assert result["results"][0]["output"] == "42"
    finally:
        await server.stop(grace=None)
