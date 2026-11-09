import httpx
import openai
import pytest
from pydantic import SecretStr

from backend.agent import llm
from backend.core.config import settings

KEY = "llm-key-should-never-leak"


class _FakeCompletions:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return type("Resp", (), {"model": self.outcome})()


class _FakeClient:
    def __init__(self, outcome):
        self.chat = type("Chat", (), {"completions": _FakeCompletions(outcome)})()


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", SecretStr(KEY))


def _status_error(code: int, message: str) -> openai.APIStatusError:
    req = httpx.Request("POST", llm.LLAMA_BASE_URL)
    resp = httpx.Response(code, request=req, json={"error": {"message": message}})
    return openai.APIStatusError(message, response=resp, body={"error": {"message": message}})


def test_client_points_at_llama_with_timeout_and_one_retry(with_key):
    c = llm.get_client()
    assert str(c.base_url) == llm.LLAMA_BASE_URL
    assert c.timeout == llm.TIMEOUT_S
    assert c.max_retries == 1
    assert llm.get_client(retry=False).max_retries == 0


def test_ping_ok(with_key, monkeypatch):
    fake = _FakeClient("Llama-4-Maverick-17B-128E-Instruct-FP8")
    monkeypatch.setattr(llm, "get_client", lambda retry=True: fake)
    r = llm.ping()
    assert r.ok and r.model == "Llama-4-Maverick-17B-128E-Instruct-FP8" and r.ms >= 0
    assert fake.chat.completions.calls[0]["max_tokens"] == 5


def test_ping_reports_http_error_without_key(with_key, monkeypatch):
    fake = _FakeClient(_status_error(404, "model not found"))
    monkeypatch.setattr(llm, "get_client", lambda retry=True: fake)
    r = llm.ping()
    assert not r.ok
    assert r.error == "HTTP 404: model not found"
    assert KEY not in repr(r)


def test_ping_without_key_never_raises(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", None)
    r = llm.ping()
    assert not r.ok and "LLM_API_KEY" in r.error
