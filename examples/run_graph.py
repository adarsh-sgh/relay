"""Run the LangGraph agent standalone (no Temporal needed)."""

from relay.demo import DemoLLM
from relay.graph import run_agent
from relay.llm import llm_from_env

if __name__ == "__main__":
    state = run_agent(llm_from_env(fallback=DemoLLM()), "compute 6*7 and report")
    print("plan:", state["plan"].steps)
    print("answer:", state["answer"].answer, f"(confidence {state['answer'].confidence})")
