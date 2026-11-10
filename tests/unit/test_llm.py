import httpx
import openai
import pytest
from pydantic import SecretStr

from backend.agent import llm
from backend.core.config import settings

KEY = "llm-key-should-never-leak"


def _resp(model: str, content: str | None = "OK", finish: str = "stop"):
    message = type("Msg", (), {"content": content})()
    choice = type("Choice", (), {"message": message, "finish_reason": finish})()
    return type("Resp", (), {"model": model, "choices": [choice]})()


class _FakeCompletions:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _FakeClient:
    def __init__(self, outcome):
        self.chat = type("Chat", (), {"completions": _FakeCompletions(outcome)})()


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", SecretStr(KEY))


def _fake(monkeypatch, outcome) -> _FakeClient:
    fake = _FakeClient(outcome)
    monkeypatch.setattr(llm, "get_client", lambda retry=True: fake)
    return fake


def _status_error(code: int, message: str) -> openai.APIStatusError:
    req = httpx.Request("POST", llm.LLM_BASE_URL)
    resp = httpx.Response(code, request=req, json={"error": {"message": message}})
    return openai.APIStatusError(message, response=resp, body={"error": {"message": message}})


def test_client_points_at_meta_model_api_with_timeout_and_one_retry(with_key):
    c = llm.get_client()
    assert str(c.base_url).rstrip("/") == "https://api.meta.ai/v1"
    assert c.timeout == llm.TIMEOUT_S == 8.0
    assert c.max_retries == 1
    assert llm.get_client(retry=False).max_retries == 0


def test_defaults_are_muse_ember_with_low_reasoning():
    assert settings.llm_model == "muse-ember-1.0"
    assert settings.llm_reasoning_effort == "low"


def test_ping_ok_sends_model_settings(with_key, monkeypatch):
    fake = _fake(monkeypatch, _resp("muse-ember-1.0"))
    r = llm.ping()
    assert r.ok and r.model == "muse-ember-1.0" and r.ms >= 0
    call = fake.chat.completions.calls[0]
    assert call["model"] == settings.llm_model
    assert call["reasoning_effort"] == settings.llm_reasoning_effort
    assert call["temperature"] == 0.1


@pytest.mark.parametrize(("content", "finish"), [(None, "length"), ("", "stop"), ("OK", "length")])
def test_ping_cut_off_or_empty_reply_is_a_failure(with_key, monkeypatch, content, finish):
    _fake(monkeypatch, _resp("muse-ember-1.0", content, finish))
    r = llm.ping()
    assert not r.ok and "cut off" in r.error


def test_ping_reports_http_error_without_key(with_key, monkeypatch):
    _fake(monkeypatch, _status_error(404, "model not found"))
    r = llm.ping()
    assert not r.ok
    assert r.error == "HTTP 404: model not found"
    assert KEY not in repr(r)


def test_ping_without_key_never_raises(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", None)
    r = llm.ping()
    assert not r.ok and "LLM_API_KEY" in r.error
