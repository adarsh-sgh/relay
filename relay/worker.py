"""Temporal worker entrypoint.

    temporal server start-dev   # terminal 1
    python -m relay.worker      # terminal 2

Uses the real OpenAI-compatible client when OPENAI_API_KEY is set,
otherwise a deterministic demo LLM. Prometheus metrics are served on
RELAY_METRICS_ADDR (default 127.0.0.1:9464) at /metrics.
"""

from __future__ import annotations

import asyncio
import logging
import os

from temporalio.client import Client
from temporalio.worker import Worker

from relay.demo import DemoLLM
from relay.llm import llm_from_env
from relay.metrics import DEFAULT_METRICS_ADDR, prometheus_runtime
from relay.workflow import TASK_QUEUE, AgentActivities, AgentPipeline


async def main(target: str = "localhost:7233") -> None:
    logging.basicConfig(level=logging.INFO)
    metrics_addr = os.environ.get("RELAY_METRICS_ADDR", DEFAULT_METRICS_ADDR)
    client = await Client.connect(target, runtime=prometheus_runtime(metrics_addr))
    activities = AgentActivities(llm_from_env(fallback=DemoLLM()))
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[AgentPipeline],
        activities=[activities.plan, activities.execute_step, activities.synthesize],
    )
    logging.info("relay worker polling %s; metrics on http://%s/metrics", TASK_QUEUE, metrics_addr)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
