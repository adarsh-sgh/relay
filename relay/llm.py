"""Pluggable LLM client interface.

MockLLM serves scripted responses so the whole engine is testable offline.
OpenAICompatibleLLM talks to any /chat/completions endpoint (OpenAI, vLLM,
Ollama, ...) and is selected automatically when OPENAI_API_KEY is set.
"""

from __future__ import annotations

import os
from typing import Protocol

Message = dict[str, str]  # {"role": ..., "content": ...}


class LLMClient(Protocol):
    def complete(self, messages: list[Message]) -> str:
        """Return the assistant text for a chat transcript."""
        ...


class MockLLM:
    """Deterministic client that pops responses off a script.

    Records every transcript it was called with, so tests can assert
    on re-prompts (e.g. that a validation error was fed back).
    """

    def __init__(self, script: list[str]):
        self._script = list(script)
        self.calls: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> str:
        self.calls.append([dict(m) for m in messages])
        if not self._script:
            raise RuntimeError("MockLLM script exhausted")
        return self._script.pop(0)


class OpenAICompatibleLLM:
    """Minimal client for any OpenAI-compatible chat completions API."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def complete(self, messages: list[Message]) -> str:
        import httpx  # lazy: keeps the Temporal workflow sandbox httpx-free

        resp = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self._model, "messages": messages},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def llm_from_env(fallback: LLMClient | None = None) -> LLMClient:
    """Real client if OPENAI_API_KEY is set, otherwise the given fallback.

    OPENAI_BASE_URL / OPENAI_MODEL override the defaults, so any
    OpenAI-compatible server works.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        return OpenAICompatibleLLM(
            api_key=api_key,
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
    if fallback is not None:
        return fallback
    raise RuntimeError("OPENAI_API_KEY not set and no fallback LLM provided")
