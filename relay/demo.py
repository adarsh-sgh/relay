"""Rule-based demo LLM so the worker and examples run without API keys."""

from __future__ import annotations

import json

from relay.llm import Message


class DemoLLM:
    """Returns a canned Plan or FinalAnswer based on the requested schema."""

    def complete(self, messages: list[Message]) -> str:
        text = " ".join(m["content"] for m in messages)
        if '"steps_taken"' in text:
            results = []
            for m in messages:
                if "Step results:" in m["content"]:
                    payload = m["content"].split("Step results:", 1)[1].split("\n", 1)[0]
                    results = json.loads(payload)
            return json.dumps(
                {
                    "answer": "; ".join(r["output"] for r in results) or "done",
                    "confidence": 0.9,
                    "steps_taken": [r["step"] for r in results],
                }
            )
        return json.dumps(
            {
                "goal": "demo task",
                "steps": ["calc: 6*7", "note: verified arithmetic"],
            }
        )
