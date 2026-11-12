"""A fake OpenAI client for agent tests (no network, P4 D15). Replies are scripted per job
(`triage` / `answer`, read from the request's json_schema name); each item is a dict (sent as
JSON), a raw string, an Exception, or `("length", dict)` for a cut-off reply."""

import json
import threading
import time
from typing import Any


def resp(content: str | None, finish: str = "stop", usage: tuple[int, int] = (900, 120)):
    msg = type("Msg", (), {"content": content})()
    choice = type("Choice", (), {"message": msg, "finish_reason": finish})()
    u = type("Usage", (), {"prompt_tokens": usage[0], "completion_tokens": usage[1]})()
    return type("Resp", (), {"choices": [choice], "usage": u, "model": "fake"})()


def triage(label: str, **fields: Any) -> dict:
    base = {"sku": None, "skus": [], "amount_cents": None, "date": None, "invoice_no": None,
            "reason": None}  # fmt: skip
    return {"label": label, "fields": base | fields}


def answer(text: str, cited: list[str], confidence: str = "high") -> dict:
    return {"answer": text, "cited_records": cited, "confidence": confidence,
            "offer_escalation": False}  # fmt: skip


class FakeLLM:
    def __init__(self, triage=(), answer=(), delay: float = 0.0, reminder=()):
        self.script = {"triage": list(triage), "answer": list(answer), "reminder": list(reminder)}
        self.delay = delay
        self.calls: list[dict] = []
        self.options: list[dict] = []
        self._lock = threading.Lock()
        self.chat = type("Chat", (), {"completions": self})()

    def with_options(self, **kw):
        self.options.append(kw)
        return self

    def create(self, **kw):
        job = kw["response_format"]["json_schema"]["name"]
        with self._lock:
            self.calls.append({"job": job, **kw})
            items = self.script[job]
            item = items.pop(0) if len(items) > 1 else items[0]  # the last one repeats
        if self.delay:
            time.sleep(self.delay)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple) and item[0] == "length":
            return resp(json.dumps(item[1]), finish="length")
        return resp(item if isinstance(item, str) else json.dumps(item))

    def jobs(self) -> list[str]:
        return [c["job"] for c in self.calls]
