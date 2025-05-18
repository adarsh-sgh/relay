"""Tiny built-in tool registry used by the execute node/activity.

A plan step is routed by prefix: "calc: <expr>" evaluates arithmetic,
anything else is recorded as a note. Real deployments register their own.
"""

from __future__ import annotations

import ast
import operator
from typing import Callable

from relay.schemas import StepResult

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_safe_eval(node.operand))
    raise ValueError(f"unsupported expression: {ast.dump(node)}")


def calc(expr: str) -> str:
    value = _safe_eval(ast.parse(expr, mode="eval"))
    return str(int(value)) if float(value).is_integer() else str(value)


def note(text: str) -> str:
    return text


TOOLS: dict[str, Callable[[str], str]] = {"calc": calc, "note": note}


def run_step(step: str) -> StepResult:
    """Route a plan step to a tool. 'calc: 2+2' -> calc('2+2')."""
    name, _, arg = step.partition(":")
    name = name.strip().lower()
    if name in TOOLS and arg:
        return StepResult(step=step, output=TOOLS[name](arg.strip()), tool_used=name)
    return StepResult(step=step, output=note(step), tool_used="note")
