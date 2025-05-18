"""Temporal worker entrypoint.

    temporal server start-dev   # terminal 1
    python -m relay.worker      # terminal 2

Uses the real OpenAI-compatible client when OPENAI_API_KEY is set,
otherwise a deterministic demo LLM.
"""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from relay.demo import DemoLLM
from relay.llm import llm_from_env
from relay.workflow import TASK_QUEUE, AgentActivities, AgentPipeline


async def main(target: str = "localhost:7233") -> None:
    logging.basicConfig(level=logging.INFO)
    client = await Client.connect(target)
    activities = AgentActivities(llm_from_env(fallback=DemoLLM()))
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[AgentPipeline],
        activities=[activities.plan, activities.execute_step, activities.synthesize],
    )
    logging.info("relay worker polling %s", TASK_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
