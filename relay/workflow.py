"""Temporal workflow: durable multi-step agent pipeline.

Each stage (plan, execute step, synthesize) is an activity with a retry
policy, so transient LLM/tool failures are retried and completed state is
never re-executed after a worker crash. An approval signal gates execution
between planning and running the plan (human-in-the-loop).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

# pass non-deterministic imports through the workflow sandbox; they are
# only used inside activities, never in workflow code
with workflow.unsafe.imports_passed_through():
    from relay.llm import LLMClient
    from relay.tracing import get_tracer

TASK_QUEUE = "relay-agent"

_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_attempts=4,
)


@dataclass
class PipelineInput:
    task: str
    require_approval: bool = True


class AgentActivities:
    """Activities bound to a pluggable LLM client (mock in tests)."""

    def __init__(self, llm: LLMClient):
        self._llm = llm
        self._tracer = get_tracer()

    @activity.defn
    async def plan(self, task: str) -> dict:
        from relay.correction import generate_validated, schema_prompt
        from relay.schemas import Plan

        with self._tracer.span("activity:plan", task=task):
            plan = generate_validated(
                self._llm,
                [
                    {"role": "system", "content": schema_prompt(Plan)},
                    {
                        "role": "user",
                        "content": (
                            "Break this task into tool steps "
                            f"('calc: <expr>' or 'note: <text>'): {task}"
                        ),
                    },
                ],
                Plan,
            )
        return plan.model_dump()

    @activity.defn
    async def execute_step(self, step: str) -> dict:
        from relay.tools import run_step

        with self._tracer.span("activity:execute_step", step=step):
            return run_step(step).model_dump()

    @activity.defn
    async def synthesize(self, args: list) -> dict:
        import json

        from relay.correction import generate_validated, schema_prompt
        from relay.schemas import FinalAnswer

        task, results = args
        with self._tracer.span("activity:synthesize", task=task):
            answer = generate_validated(
                self._llm,
                [
                    {"role": "system", "content": schema_prompt(FinalAnswer)},
                    {
                        "role": "user",
                        "content": (
                            f"Task: {task}\nStep results: {json.dumps(results)}\n"
                            "Synthesize the final answer and rate your confidence."
                        ),
                    },
                ],
                FinalAnswer,
            )
        return answer.model_dump()


@workflow.defn
class AgentPipeline:
    """Durable pipeline: plan -> [await approval] -> execute -> synthesize."""

    def __init__(self) -> None:
        self._status = "starting"
        self._decision: str | None = None

    @workflow.run
    async def run(self, input: PipelineInput) -> dict:
        self._status = "planning"
        plan = await workflow.execute_activity(
            AgentActivities.plan,
            input.task,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_RETRY,
        )

        if input.require_approval:
            self._status = "awaiting_approval"
            await workflow.wait_condition(lambda: self._decision is not None)
            if self._decision == "reject":
                self._status = "rejected"
                return {"status": "rejected", "plan": plan}

        self._status = "executing"
        results = []
        for step in plan["steps"]:
            results.append(
                await workflow.execute_activity(
                    AgentActivities.execute_step,
                    step,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=_RETRY,
                )
            )

        self._status = "synthesizing"
        answer = await workflow.execute_activity(
            AgentActivities.synthesize,
            [input.task, results],
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_RETRY,
        )

        self._status = "completed"
        return {"status": "completed", "plan": plan, "results": results, "answer": answer}

    @workflow.signal
    def approve(self) -> None:
        self._decision = "approve"

    @workflow.signal
    def reject(self) -> None:
        self._decision = "reject"

    @workflow.query
    def status(self) -> str:
        return self._status
