"""Temporal workflow: durable multi-step agent pipeline.

Each stage (plan, execute step, synthesize) is an activity with a retry
policy, so transient LLM/tool failures are retried and completed state is
never re-executed after a worker crash. A decision signal gates execution
between planning and running the plan (human-in-the-loop).

Every status change and decision is appended to an audit trail that is
queryable while the run is live and returned with the result. Finished
runs and per-activity latency are reported as Prometheus metrics
(relay/metrics.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

# pass non-deterministic imports through the workflow sandbox; they are
# only used inside activities, never in workflow code
with workflow.unsafe.imports_passed_through():
    from relay.llm import LLMClient
    from relay.metrics import RUNS, step_timer
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
    # provenance, set by the console when a run starts from a template
    template: str = ""
    requested_by: str = ""
    params: dict[str, str] = field(default_factory=dict)


@dataclass
class Decision:
    verdict: str  # "approve" | "reject"
    actor: str = ""
    reason: str = ""


class AgentActivities:
    """Activities bound to a pluggable LLM client (mock in tests)."""

    def __init__(self, llm: LLMClient):
        self._llm = llm
        self._tracer = get_tracer()

    @activity.defn
    async def plan(self, task: str) -> dict:
        from relay.correction import generate_validated, schema_prompt
        from relay.schemas import Plan

        with step_timer("plan"), self._tracer.span("activity:plan", task=task):
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

        with step_timer("execute_step"), self._tracer.span("activity:execute_step", step=step):
            return run_step(step).model_dump()

    @activity.defn
    async def synthesize(self, args: list) -> dict:
        import json

        from relay.correction import generate_validated, schema_prompt
        from relay.schemas import FinalAnswer

        task, results = args
        with step_timer("synthesize"), self._tracer.span("activity:synthesize", task=task):
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
    """Durable pipeline: plan -> [await decision] -> execute -> synthesize."""

    def __init__(self) -> None:
        self._status = "starting"
        self._decision: Decision | None = None
        self._audit: list[dict] = []
        self._input: PipelineInput | None = None

    def _set_status(self, status: str, **detail: str) -> None:
        self._status = status
        self._audit.append({"event": status, "at": workflow.now().isoformat(), **detail})

    def _count_run(self, status: str) -> None:
        template = self._input.template if self._input and self._input.template else "adhoc"
        workflow.metric_meter().create_counter(RUNS, "finished runs by outcome").add(
            1, {"template": template, "status": status}
        )

    @workflow.run
    async def run(self, input: PipelineInput) -> dict:
        self._input = input
        self._set_status("started", template=input.template, requested_by=input.requested_by)
        try:
            return await self._run(input)
        except ActivityError as exc:
            self._set_status("failed", error=str(exc.cause or exc))
            self._count_run("failed")
            raise

    async def _run(self, input: PipelineInput) -> dict:
        self._set_status("planning")
        plan = await workflow.execute_activity(
            AgentActivities.plan,
            input.task,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_RETRY,
        )

        if input.require_approval:
            self._set_status("awaiting_approval")
            await workflow.wait_condition(lambda: self._decision is not None)
            d = self._decision
            self._audit.append(
                {
                    "event": "decision",
                    "at": workflow.now().isoformat(),
                    "verdict": d.verdict,
                    "actor": d.actor,
                    "reason": d.reason,
                }
            )
            if d.verdict == "reject":
                self._set_status("rejected")
                self._count_run("rejected")
                return {"status": "rejected", "plan": plan, "audit": self._audit}

        self._set_status("executing")
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

        self._set_status("synthesizing")
        answer = await workflow.execute_activity(
            AgentActivities.synthesize,
            [input.task, results],
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_RETRY,
        )

        self._set_status("completed")
        self._count_run("completed")
        return {
            "status": "completed",
            "plan": plan,
            "results": results,
            "answer": answer,
            "audit": self._audit,
        }

    @workflow.signal
    def decide(self, decision: Decision) -> None:
        if self._decision is None and decision.verdict in ("approve", "reject"):
            self._decision = decision

    @workflow.signal
    def approve(self) -> None:
        self.decide(Decision(verdict="approve"))

    @workflow.signal
    def reject(self) -> None:
        self.decide(Decision(verdict="reject"))

    @workflow.query
    def status(self) -> str:
        return self._status

    @workflow.query
    def audit(self) -> list[dict]:
        return self._audit

    @workflow.query
    def meta(self) -> dict:
        i = self._input
        if i is None:
            return {}
        return {"template": i.template, "requested_by": i.requested_by, "params": i.params}
