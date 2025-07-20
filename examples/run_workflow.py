"""Start a durable pipeline run against a local Temporal dev server.

Prereqs: `temporal server start-dev` and `python -m relay.worker` running.
The workflow pauses at awaiting_approval; approve it with:

    python examples/approve.py <workflow-id>
"""

import asyncio
import sys
import uuid

from temporalio.client import Client

from relay.workflow import TASK_QUEUE, AgentPipeline, PipelineInput


async def main() -> None:
    task = sys.argv[1] if len(sys.argv) > 1 else "compute 6*7 and report"
    client = await Client.connect("localhost:7233")
    wf_id = f"relay-{uuid.uuid4().hex[:8]}"
    handle = await client.start_workflow(
        AgentPipeline.run,
        PipelineInput(task=task),
        id=wf_id,
        task_queue=TASK_QUEUE,
    )
    print(f"started {wf_id}; status: {await handle.query(AgentPipeline.status)}")
    print(f"approve with: python examples/approve.py {wf_id}")
    print("result:", await handle.result())


if __name__ == "__main__":
    asyncio.run(main())
