"""Send the human-in-the-loop approval signal to a paused workflow."""

import asyncio
import sys

from temporalio.client import Client

from relay.workflow import AgentPipeline


async def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python examples/approve.py <workflow-id> [reject]")
    client = await Client.connect("localhost:7233")
    handle = client.get_workflow_handle_for(AgentPipeline.run, sys.argv[1])
    if len(sys.argv) > 2 and sys.argv[2] == "reject":
        await handle.signal(AgentPipeline.reject)
        print("rejected")
    else:
        await handle.signal(AgentPipeline.approve)
        print("approved")


if __name__ == "__main__":
    asyncio.run(main())
