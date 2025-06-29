"""LangGraph orchestration: plan -> execute -> validate.

The validate node synthesizes a FinalAnswer and, if confidence is low,
routes back to plan for one revision before giving up.
"""

from __future__ import annotations

import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from relay.correction import generate_validated, schema_prompt
from relay.llm import LLMClient
from relay.schemas import FinalAnswer, Plan
from relay.tools import run_step
from relay.tracing import get_tracer

CONFIDENCE_THRESHOLD = 0.5
MAX_REVISIONS = 1


class AgentState(TypedDict, total=False):
    task: str
    plan: Plan
    results: list[dict]
    answer: FinalAnswer
    revisions: int


def build_graph(llm: LLMClient):
    """Compile the plan/execute/validate graph bound to an LLM client."""
    tracer = get_tracer()

    def plan_node(state: AgentState) -> AgentState:
        with tracer.span("node:plan", task=state["task"]):
            feedback = ""
            if state.get("answer") is not None:
                feedback = (
                    "\nA previous attempt scored low confidence "
                    f"({state['answer'].confidence}); produce a better plan."
                )
            plan = generate_validated(
                llm,
                [
                    {"role": "system", "content": schema_prompt(Plan)},
                    {
                        "role": "user",
                        "content": (
                            f"Break this task into tool steps "
                            f"('calc: <expr>' or 'note: <text>'): {state['task']}"
                            + feedback
                        ),
                    },
                ],
                Plan,
            )
        return {"plan": plan, "results": []}

    def execute_node(state: AgentState) -> AgentState:
        with tracer.span("node:execute"):
            results = [run_step(step).model_dump() for step in state["plan"].steps]
        return {"results": results}

    def validate_node(state: AgentState) -> AgentState:
        with tracer.span("node:validate"):
            answer = generate_validated(
                llm,
                [
                    {"role": "system", "content": schema_prompt(FinalAnswer)},
                    {
                        "role": "user",
                        "content": (
                            f"Task: {state['task']}\n"
                            f"Step results: {json.dumps(state['results'])}\n"
                            "Synthesize the final answer and rate your confidence."
                        ),
                    },
                ],
                FinalAnswer,
            )
        return {"answer": answer, "revisions": state.get("revisions", 0) + 1}

    def route(state: AgentState) -> str:
        if state["answer"].confidence >= CONFIDENCE_THRESHOLD:
            return END
        if state["revisions"] > MAX_REVISIONS:
            return END
        return "plan"

    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.add_node("execute", execute_node)
    graph.add_node("validate", validate_node)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "execute")
    graph.add_edge("execute", "validate")
    graph.add_conditional_edges("validate", route, {END: END, "plan": "plan"})
    return graph.compile()


def run_agent(llm: LLMClient, task: str) -> AgentState:
    return build_graph(llm).invoke({"task": task})
