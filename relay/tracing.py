"""Langfuse tracing hooks.

If LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are set (and langfuse is
installed) spans are shipped to Langfuse; otherwise every call is a no-op.
Callers never branch on whether tracing is on.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator


class Tracer:
    """Thin facade over the Langfuse client with a no-op fallback."""

    def __init__(self) -> None:
        self._client = None
        if os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"):
            try:
                from langfuse import Langfuse

                self._client = Langfuse()
            except ImportError:
                pass

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @contextmanager
    def span(self, name: str, **metadata: Any) -> Iterator[None]:
        if self._client is None:
            yield
            return
        with self._client.start_as_current_span(name=name, metadata=metadata or None):
            yield

    def generation(
        self,
        name: str,
        *,
        input: Any = None,
        output: Any = None,
        model: str | None = None,
        **metadata: Any,
    ) -> None:
        """Record a completed LLM generation."""
        if self._client is None:
            return
        with self._client.start_as_current_generation(
            name=name, model=model, input=input, metadata=metadata or None
        ) as gen:
            gen.update(output=output)

    def event(self, name: str, **metadata: Any) -> None:
        if self._client is None:
            return
        self._client.create_event(name=name, metadata=metadata or None)

    def flush(self) -> None:
        if self._client is not None:
            self._client.flush()


_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    global _tracer
    if _tracer is None:
        _tracer = Tracer()
    return _tracer
