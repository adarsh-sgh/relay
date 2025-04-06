"""Schema-validated self-correction loop.

The LLM is asked for JSON matching a Pydantic model. If parsing or
validation fails, the raw output and the validation error are appended to
the transcript and the model is re-prompted, up to max_retries times.
"""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from relay.llm import LLMClient, Message
from relay.tracing import Tracer, get_tracer

T = TypeVar("T", bound=BaseModel)

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class SchemaCorrectionError(Exception):
    """Raised when output still fails validation after all retries."""

    def __init__(self, attempts: int, last_error: str):
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"invalid output after {attempts} attempts: {last_error}")


def _extract_json(text: str) -> str:
    """Tolerate markdown fences around the JSON payload."""
    match = _JSON_BLOCK.search(text)
    return (match.group(1) if match else text).strip()


def schema_prompt(model_cls: type[BaseModel]) -> str:
    return (
        "Respond with a single JSON object matching this JSON schema, "
        "no prose:\n" + json.dumps(model_cls.model_json_schema(), indent=2)
    )


def generate_validated(
    llm: LLMClient,
    messages: list[Message],
    model_cls: type[T],
    max_retries: int = 2,
    tracer: Tracer | None = None,
) -> T:
    """Call the LLM until output validates against model_cls.

    max_retries counts re-prompts after the first attempt, so the LLM is
    called at most max_retries + 1 times.
    """
    tracer = tracer or get_tracer()
    transcript = list(messages)
    last_error = ""
    attempts = max_retries + 1
    for attempt in range(attempts):
        with tracer.span(f"generate:{model_cls.__name__}", attempt=attempt):
            raw = llm.complete(transcript)
            tracer.generation(
                f"llm:{model_cls.__name__}", input=transcript[-1]["content"], output=raw
            )
        try:
            return model_cls.model_validate_json(_extract_json(raw))
        except (ValidationError, ValueError) as exc:
            last_error = str(exc)
            tracer.event("validation_failed", schema=model_cls.__name__, error=last_error)
            transcript = transcript + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        "Your previous output failed validation with this error:\n"
                        f"{last_error}\n"
                        "Fix it. " + schema_prompt(model_cls)
                    ),
                },
            ]
    raise SchemaCorrectionError(attempts, last_error)
