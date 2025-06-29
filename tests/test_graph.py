"""Full LangGraph run: plan -> execute tools -> validate, with an invalid
plan corrected mid-graph and a low-confidence answer triggering revision."""

import json

from relay.graph import run_agent
from relay.llm import MockLLM

PLAN = json.dumps({"goal": "compute", "steps": ["calc: 6*7", "note: sanity check"]})
LOW = json.dumps({"answer": "unsure", "confidence": 0.2, "steps_taken": ["calc: 6*7"]})
HIGH = json.dumps(
    {"answer": "42", "confidence": 0.95, "steps_taken": ["calc: 6*7", "note: sanity check"]}
)


def test_graph_happy_path_with_inline_self_correction():
    llm = MockLLM(
        [
            json.dumps({"goal": "compute", "steps": []}),  # invalid: min 1 step
            PLAN,  # corrected plan
            HIGH,  # confident answer -> END
        ]
    )
    state = run_agent(llm, "compute 6*7 and report")

    assert state["plan"].steps == ["calc: 6*7", "note: sanity check"]
    assert [r["output"] for r in state["results"]] == ["42", "sanity check"]
    assert [r["tool_used"] for r in state["results"]] == ["calc", "note"]
    assert state["answer"].answer == "42"
    assert state["revisions"] == 1


def test_low_confidence_routes_back_to_plan_once():
    llm = MockLLM([PLAN, LOW, PLAN, HIGH])
    state = run_agent(llm, "compute 6*7 and report")

    assert state["answer"].confidence == 0.95
    assert state["revisions"] == 2
    # revision plan prompt mentions the low-confidence feedback
    replan_prompt = llm.calls[2][-1]["content"]
    assert "low confidence" in replan_prompt
