"""End-to-end self-correction: invalid output -> re-prompt with error -> valid."""

import json

import pytest

from relay.correction import SchemaCorrectionError, generate_validated
from relay.llm import MockLLM
from relay.schemas import FinalAnswer

PROMPT = [{"role": "user", "content": "answer the question"}]


def test_corrects_invalid_output_and_feeds_back_validation_error():
    good = json.dumps({"answer": "42", "confidence": 0.9, "steps_taken": ["calc"]})
    llm = MockLLM(
        [
            "not json at all",  # attempt 1: parse failure
            json.dumps({"answer": "42", "confidence": 3.0, "steps_taken": []}),  # out of range
            f"```json\n{good}\n```",  # valid, fenced
        ]
    )

    result = generate_validated(llm, PROMPT, FinalAnswer, max_retries=2)

    assert result == FinalAnswer(answer="42", confidence=0.9, steps_taken=["calc"])
    assert len(llm.calls) == 3
    # second call carries the raw bad output plus a validation error re-prompt
    retry_msg = llm.calls[1][-1]["content"]
    assert "failed validation" in retry_msg and "json" in retry_msg.lower()
    # third call complains specifically about the range violation
    assert "less than or equal to 1" in llm.calls[2][-1]["content"]


def test_raises_after_exhausting_retries():
    llm = MockLLM(["bad", "still bad", "worse"])
    with pytest.raises(SchemaCorrectionError) as exc:
        generate_validated(llm, PROMPT, FinalAnswer, max_retries=2)
    assert exc.value.attempts == 3
    assert len(llm.calls) == 3
