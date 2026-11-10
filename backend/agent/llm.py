"""Meta Model API (Muse models) through the OpenAI client (OpenAI-compatible endpoint).

P0: client + ping. P4 adds the agent jobs on top. Never logs or returns the API key.
Muse models reason before answering: a reply cut off by the token limit
(finish_reason="length") or with no text is a failure, never a partial answer.
"""

import time
from typing import Any, NamedTuple

import openai
from openai import OpenAI

from backend.core.config import settings

LLM_BASE_URL = "https://api.meta.ai/v1"
TIMEOUT_S = 8.0
PING_MAX_TOKENS = 400  # room for the model's reasoning + a one-word answer


class LLMNotConfiguredError(RuntimeError):
    pass


class PingResult(NamedTuple):
    ok: bool
    model: str
    ms: int
    error: str | None = None  # "HTTP 404: <message>" etc. — never contains the key


def get_client(*, retry: bool = True) -> OpenAI:
    if settings.llm_api_key is None:
        raise LLMNotConfiguredError("LLM_API_KEY is not set")
    return OpenAI(
        base_url=LLM_BASE_URL,
        api_key=settings.llm_api_key.get_secret_value(),
        timeout=TIMEOUT_S,
        max_retries=1 if retry else 0,
    )


def call_kwargs() -> dict[str, Any]:
    """Model settings every call uses (P4 jobs add messages, tools, response_format)."""
    return {
        "model": settings.llm_model,
        "reasoning_effort": settings.llm_reasoning_effort,
        "temperature": 0.1,
    }


def ping(*, retry: bool = False) -> PingResult:
    """One tiny completion to prove the key + model answer. Never raises."""
    model = settings.llm_model
    start = time.perf_counter()
    try:
        resp = get_client(retry=retry).chat.completions.create(
            **call_kwargs(),
            messages=[{"role": "user", "content": "Reply with the word OK"}],
            max_tokens=PING_MAX_TOKENS,
        )
        choice = resp.choices[0]
        if choice.finish_reason == "length" or not (choice.message.content or "").strip():
            return PingResult(False, model, _ms(start), "no answer (cut off by token limit)")
        return PingResult(True, resp.model or model, _ms(start))
    except LLMNotConfiguredError as e:
        return PingResult(False, model, _ms(start), str(e))
    except openai.APIStatusError as e:
        return PingResult(False, model, _ms(start), f"HTTP {e.status_code}: {_message(e)}")
    except openai.APITimeoutError:
        return PingResult(False, model, _ms(start), f"timeout after {TIMEOUT_S:.0f}s")
    except openai.APIConnectionError as e:
        return PingResult(False, model, _ms(start), f"connection error: {type(e).__name__}")


def list_models() -> list[str]:
    """Model ids the key can use (for diagnosing a 404 on the configured model)."""
    return sorted(m.id for m in get_client(retry=False).models.list())


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _message(e: openai.APIStatusError) -> str:
    body = e.body if isinstance(e.body, dict) else {}
    err = body.get("error") if isinstance(body.get("error"), dict) else body
    return str(err.get("message") or e.message)[:300]
