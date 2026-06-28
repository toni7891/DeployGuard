from deployguard.config import AppConfig
from deployguard.llm.base import LLMAdapter
from deployguard.llm.bedrock import BedrockAdapter
from deployguard.llm.lmstudio import LMStudioAdapter, LMStudioConnectionError

__all__ = [
    "LLMAdapter",
    "LMStudioAdapter",
    "LMStudioConnectionError",
    "BedrockAdapter",
    "get_adapter",
]


def get_adapter(config: AppConfig) -> LLMAdapter:
    """Return the LLM adapter for config.llm.backend. Caller must check backend is set."""
    if config.llm.backend == "local":
        return LMStudioAdapter(model=config.llm.model, temperature=config.llm.temperature)
    if config.llm.backend == "bedrock":
        return BedrockAdapter(model=config.llm.model, temperature=config.llm.temperature)
    raise ValueError(f"Unknown llm.backend: {config.llm.backend!r}")
