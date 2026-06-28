"""Abstract LLM adapter — the LLM is always the drafter, the guard always has final say."""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMAdapter(ABC):
    @abstractmethod
    def generate(self, prompt: str, schema: dict) -> dict:
        """Generate structured output matching the given JSON Schema.

        Raises on a non-2xx response, a connection failure, or output that
        doesn't parse as JSON. Callers decide how to handle failure (e.g.
        falling back to Jinja2 templates) — this layer never falls back itself.
        """

    def is_available(self) -> bool:
        """Whether this backend can be reached right now.

        Cloud backends are available by definition (auth failures surface in
        generate()); local backends override this with a real health check.
        """
        return True
