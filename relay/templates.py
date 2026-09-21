"""Reusable workflow templates.

A template is a named, versioned task definition with typed parameters,
an owning team and an SLO. Operators start runs by template name plus
parameters; they never write a prompt. Templates are plain JSON files in
a directory (templates/ by default) so a non-engineer can add one via PR.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError


class TemplateParam(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str = ""
    required: bool = True
    default: str | None = None


class SLO(BaseModel):
    """Targets the owner commits to; measured from the Prometheus metrics."""

    success_rate: float = Field(ge=0.0, le=1.0)
    p95_step_latency_ms: int = Field(gt=0)


class WorkflowTemplate(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    title: str
    description: str = ""
    owner: str
    task: str  # python format string, {param} placeholders
    params: list[TemplateParam] = []
    require_approval: bool = True
    slo: SLO

    def render(self, values: dict[str, str]) -> str:
        """Fill the task string; missing required or unknown params are errors."""
        known = {p.name: p for p in self.params}
        unknown = sorted(set(values) - set(known))
        if unknown:
            raise TemplateParamError(f"unknown params: {', '.join(unknown)}")
        merged: dict[str, str] = {}
        missing = []
        for p in self.params:
            if p.name in values:
                merged[p.name] = values[p.name]
            elif p.default is not None:
                merged[p.name] = p.default
            elif p.required:
                missing.append(p.name)
            else:
                merged[p.name] = ""
        if missing:
            raise TemplateParamError(f"missing params: {', '.join(missing)}")
        return self.task.format(**merged)


class TemplateError(Exception):
    pass


class TemplateNotFound(TemplateError):
    pass


class TemplateParamError(TemplateError):
    pass


class TemplateRegistry:
    def __init__(self, templates: list[WorkflowTemplate]):
        self._by_name = {t.name: t for t in templates}

    @classmethod
    def load(cls, directory: str | Path) -> "TemplateRegistry":
        directory = Path(directory)
        templates = []
        for path in sorted(directory.glob("*.json")):
            try:
                templates.append(WorkflowTemplate.model_validate_json(path.read_text()))
            except (ValidationError, json.JSONDecodeError) as exc:
                raise TemplateError(f"{path.name}: {exc}") from exc
        return cls(templates)

    def get(self, name: str) -> WorkflowTemplate:
        try:
            return self._by_name[name]
        except KeyError:
            raise TemplateNotFound(name) from None

    def list(self) -> list[WorkflowTemplate]:
        return list(self._by_name.values())
