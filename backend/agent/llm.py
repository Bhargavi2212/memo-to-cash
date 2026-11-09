"""Meta Llama API through the OpenAI client (OpenAI-compatible endpoint).

P0: client + ping. P4 adds the agent jobs on top. Never logs or returns the API key.
"""

import time
from typing import NamedTuple

import openai
from openai import OpenAI

from backend.core.config import settings

LLAMA_BASE_URL = "https://api.llama.com/compat/v1/"
TIMEOUT_S = 8.0


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
        base_url=LLAMA_BASE_URL,
        api_key=settings.llm_api_key.get_secret_value(),
        timeout=TIMEOUT_S,
        max_retries=1 if retry else 0,
    )


def ping(*, retry: bool = False) -> PingResult:
    """One tiny completion to prove the key + model work. Never raises."""
    model = settings.llm_model
    start = time.perf_counter()
    try:
        resp = get_client(retry=retry).chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with the word OK"}],
            max_tokens=5,
            temperature=0,
        )
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
