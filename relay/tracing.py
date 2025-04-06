"""Tracing facade.

Currently a no-op; a real exporter (Langfuse) will be wired behind the
same interface so callers never branch on whether tracing is enabled.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator


class Tracer:
    @property
    def enabled(self) -> bool:
        return False

    @contextmanager
    def span(self, name: str, **metadata: Any) -> Iterator[None]:
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
        return

    def event(self, name: str, **metadata: Any) -> None:
        return

    def flush(self) -> None:
        return


_tracer: Tracer | None = None


def get_tracer() -> Tracer:
    global _tracer
    if _tracer is None:
        _tracer = Tracer()
    return _tracer
