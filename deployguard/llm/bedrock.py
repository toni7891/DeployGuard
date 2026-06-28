"""Cloud LLM backend — AWS Bedrock. Implemented in Step 31."""
from __future__ import annotations

from deployguard.llm.base import LLMAdapter

DEFAULT_MODEL = "anthropic.claude-3-5-sonnet-20241022-v2:0"


class BedrockAdapter(LLMAdapter):
    def __init__(self, model: str = DEFAULT_MODEL, temperature: float = 0.2):
        self.model = model
        self.temperature = temperature

    def generate(self, prompt: str, schema: dict) -> dict:
        raise NotImplementedError("Bedrock backend lands in Step 31.")
