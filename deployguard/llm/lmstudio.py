"""Local LLM backend — talks to LM Studio's OpenAI-compatible server."""
from __future__ import annotations

import json

import requests

from deployguard.llm.base import LLMAdapter

DEFAULT_BASE_URL = "http://localhost:1234"
DEFAULT_MODEL = "qwen2.5-coder-14b-instruct"


class LMStudioConnectionError(RuntimeError):
    """LM Studio's local server could not be reached."""


# ── Private HTTP wrappers (monkeypatch-friendly in tests) ──────────────────────

def _http_get(url: str, timeout: float) -> requests.Response:
    return requests.get(url, timeout=timeout)


def _http_post(url: str, payload: dict, timeout: float) -> requests.Response:
    return requests.post(url, json=payload, timeout=timeout)


class LMStudioAdapter(LLMAdapter):
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.2,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 120.0,
    ):
        self.model = model
        self.temperature = temperature
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def is_available(self) -> bool:
        """Quick health check — does LM Studio's local server respond at all?

        Used to decide whether to attempt LLM drafting or go straight to the
        Jinja2 fallback. A short timeout keeps `dg init` snappy when LM Studio
        isn't running (the common case).
        """
        try:
            resp = _http_get(f"{self.base_url}/v1/models", timeout=1.5)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def generate(self, prompt: str, schema: dict) -> dict:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "output", "strict": True, "schema": schema},
            },
        }
        try:
            resp = _http_post(
                f"{self.base_url}/v1/chat/completions", payload, self.timeout
            )
        except requests.RequestException as exc:
            raise LMStudioConnectionError(
                f"Could not reach LM Studio at {self.base_url}: {exc}"
            ) from exc

        if resp.status_code != 200:
            raise RuntimeError(
                f"LM Studio returned HTTP {resp.status_code}: {resp.text[:500]}"
            )

        body = resp.json()
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"Unexpected LM Studio response shape: {body}") from exc

        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"LM Studio did not return valid JSON: {content[:500]}"
            ) from exc
