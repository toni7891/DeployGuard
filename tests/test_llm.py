"""Tests for llm/ — LM Studio adapter, Bedrock stub, and backend dispatch."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

import deployguard.llm.lmstudio as _lmstudio_mod
from deployguard.config import AppConfig, LLMConfig
from deployguard.llm import BedrockAdapter, LMStudioAdapter, get_adapter
from deployguard.llm.lmstudio import LMStudioConnectionError


def _chat_response(content: str, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = content
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


# ── LMStudioAdapter.is_available ────────────────────────────────────────────

def test_is_available_true_when_server_responds(monkeypatch):
    monkeypatch.setattr(
        _lmstudio_mod, "_http_get", lambda url, timeout: MagicMock(status_code=200)
    )
    assert LMStudioAdapter().is_available() is True


def test_is_available_false_on_connection_error(monkeypatch):
    def _fail(url, timeout):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(_lmstudio_mod, "_http_get", _fail)
    assert LMStudioAdapter().is_available() is False


def test_is_available_false_on_non_200(monkeypatch):
    monkeypatch.setattr(
        _lmstudio_mod, "_http_get", lambda url, timeout: MagicMock(status_code=500)
    )
    assert LMStudioAdapter().is_available() is False


# ── LMStudioAdapter.generate ─────────────────────────────────────────────────

def test_generate_returns_parsed_dict(monkeypatch):
    monkeypatch.setattr(
        _lmstudio_mod,
        "_http_post",
        lambda url, payload, timeout: _chat_response('{"content": "FROM python:3.11-slim"}'),
    )
    result = LMStudioAdapter().generate("draft a dockerfile", {"type": "object"})
    assert result == {"content": "FROM python:3.11-slim"}


def test_generate_sends_model_and_schema(monkeypatch):
    captured = {}

    def _post(url, payload, timeout):
        captured["url"] = url
        captured["payload"] = payload
        return _chat_response('{"content": "x"}')

    monkeypatch.setattr(_lmstudio_mod, "_http_post", _post)
    schema = {"type": "object", "properties": {"content": {"type": "string"}}}
    LMStudioAdapter(model="qwen2.5-coder-14b-instruct").generate("hi", schema)

    assert captured["url"] == "http://localhost:1234/v1/chat/completions"
    assert captured["payload"]["model"] == "qwen2.5-coder-14b-instruct"
    assert captured["payload"]["response_format"]["json_schema"]["schema"] == schema


def test_generate_raises_on_non_200(monkeypatch):
    monkeypatch.setattr(
        _lmstudio_mod,
        "_http_post",
        lambda url, payload, timeout: _chat_response("server error", status_code=500),
    )
    with pytest.raises(RuntimeError):
        LMStudioAdapter().generate("hi", {"type": "object"})


def test_generate_raises_on_invalid_json_content(monkeypatch):
    monkeypatch.setattr(
        _lmstudio_mod,
        "_http_post",
        lambda url, payload, timeout: _chat_response("not json"),
    )
    with pytest.raises(RuntimeError):
        LMStudioAdapter().generate("hi", {"type": "object"})


def test_generate_raises_connection_error_on_request_exception(monkeypatch):
    def _fail(url, payload, timeout):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(_lmstudio_mod, "_http_post", _fail)
    with pytest.raises(LMStudioConnectionError):
        LMStudioAdapter().generate("hi", {"type": "object"})


# ── Bedrock stub ──────────────────────────────────────────────────────────────

def test_bedrock_generate_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        BedrockAdapter().generate("hi", {"type": "object"})


# ── get_adapter dispatch ──────────────────────────────────────────────────────

def test_get_adapter_returns_lmstudio_for_local_backend():
    config = AppConfig(llm=LLMConfig(backend="local", model="my-model"))
    adapter = get_adapter(config)
    assert isinstance(adapter, LMStudioAdapter)
    assert adapter.model == "my-model"


def test_get_adapter_returns_bedrock_for_bedrock_backend():
    config = AppConfig(llm=LLMConfig(backend="bedrock"))
    assert isinstance(get_adapter(config), BedrockAdapter)


def test_get_adapter_raises_when_backend_unset():
    config = AppConfig(llm=LLMConfig(backend=None))
    with pytest.raises(ValueError):
        get_adapter(config)
