"""gRPC control plane: start / status / approve pipelines over gRPC.

Thin proxy in front of Temporal so non-Python callers (see go-client/)
can drive workflows without a Temporal SDK.

    python -m relay.grpc_server   # listens on :50051
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import grpc
from temporalio.client import Client

from relay.relaypb import relay_pb2, relay_pb2_grpc
from relay.workflow import TASK_QUEUE, AgentPipeline, PipelineInput


class RelayControlService(relay_pb2_grpc.RelayControlServicer):
    def __init__(self, client: Client, task_queue: str = TASK_QUEUE):
        self._client = client
        self._task_queue = task_queue

    async def StartRun(self, request, context):
        wf_id = f"relay-{uuid.uuid4().hex[:8]}"
        await self._client.start_workflow(
            AgentPipeline.run,
            PipelineInput(task=request.task, require_approval=request.require_approval),
            id=wf_id,
            task_queue=self._task_queue,
        )
        return relay_pb2.StartRunReply(workflow_id=wf_id)

    async def GetStatus(self, request, context):
        handle = self._client.get_workflow_handle_for(AgentPipeline.run, request.workflow_id)
        status = await handle.query(AgentPipeline.status)
        return relay_pb2.StatusReply(status=status)

    async def Approve(self, request, context):
        handle = self._client.get_workflow_handle_for(AgentPipeline.run, request.workflow_id)
        if request.reject:
            await handle.signal(AgentPipeline.reject)
            return relay_pb2.ApproveReply(status="rejected")
        await handle.signal(AgentPipeline.approve)
        return relay_pb2.ApproveReply(status="approved")


async def serve_grpc(client: Client, port: int = 50051) -> grpc.aio.Server:
    server = grpc.aio.server()
    relay_pb2_grpc.add_RelayControlServicer_to_server(RelayControlService(client), server)
    server.add_insecure_port(f"[::]:{port}")
    await server.start()
    return server


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    client = await Client.connect("localhost:7233")
    server = await serve_grpc(client)
    logging.info("relay grpc control plane on :50051")
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(main())
