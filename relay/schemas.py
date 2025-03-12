"""Structured outputs the agent must produce at each stage."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Plan(BaseModel):
    """Output of the planning step."""

    goal: str
    steps: list[str] = Field(min_length=1, max_length=10)


class StepResult(BaseModel):
    """Output of executing one plan step."""

    step: str
    output: str
    tool_used: str


class FinalAnswer(BaseModel):
    """Validated final output of the pipeline."""

    answer: str
    confidence: float = Field(ge=0.0, le=1.0)
    steps_taken: list[str]
