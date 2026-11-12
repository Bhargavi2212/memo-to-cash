"""PayPal REST client (P1 D14–D16, D19). Sandbox only — the base URL comes from settings,
which refuse anything but the sandbox (invariant 6).

- OAuth token cached as plain module data until `expires_in - 60 s` (CLAUDE.md §4);
  a 401 drops the token and retries once.
- An `httpx.AsyncClient` per operation (no module-level client or lock), 8 s per call.
- Every write must carry `PayPal-Request-Id` (invariant 4); retries reuse it. PayPal keeps
  it only ~6 h (TRD §6b-5), so it is a short-window guard, not the recovery mechanism.
- Retries: 3× with 0.5/1/2 s backoff on 5xx, 429 and timeouts; 4xx never.
- Errors → `PayPalError(name, message, debug_id, details, status, transient)` — the shape
  confirmed via APIMatic ContextMatic (docs/APIMATIC.md #2).
- Logs: method, path, status, ms, debug_id. Never bodies (emails), tokens or secrets.
"""

import asyncio
import logging
import time
from typing import Any

import httpx

from backend.core.config import Settings, get_settings

log = logging.getLogger("paypal")

TIMEOUT_S = 8.0
BACKOFF_S = (0.5, 1.0, 2.0)
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_tokens: dict[
    tuple[str, str], tuple[str, float]
] = {}  # (base_url, client_id) -> (token, expires_at)
_sleep = asyncio.sleep  # patched in tests


def _now() -> float:
    return time.monotonic()


# One token fetch at a time per (base_url, client_id): parallel calls on a cold cache wait for
# the first fetch instead of each fetching their own (phase M: 7 at once). A lock belongs to an
# event loop, so it's recreated when the loop changes (tests run many loops).
_token_locks: dict[tuple[str, str], tuple[asyncio.AbstractEventLoop, asyncio.Lock]] = {}


def _token_lock(key: tuple[str, str]) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    held = _token_locks.get(key)
    if held is None or held[0] is not loop:
        held = (loop, asyncio.Lock())
        _token_locks[key] = held
    return held[1]


def clear_token_cache() -> None:
    _tokens.clear()
    _token_locks.clear()


class PayPalNotConfiguredError(RuntimeError):
    def __init__(self, missing: list[str]):
        super().__init__(f"PayPal settings missing: {', '.join(missing)}")
        self.missing = missing


class PayPalError(Exception):
    """A PayPal failure. `transient` = worth retrying later (5xx, 429, timeout) → 503;
    otherwise deterministic (4xx) → 502 on /demo/start (D16)."""

    def __init__(
        self,
        name: str,
        message: str,
        debug_id: str | None,
        details: list[Any],
        status: int | None,
        transient: bool = False,
    ):
        super().__init__(f"{status} {name}: {message} (debug_id={debug_id})")
        self.name = name
        self.message = message
        self.debug_id = debug_id
        self.details = details
        self.status = status
        self.transient = transient


def request_id(settings: Settings, kind: str, uid: object, attempt: int | None = None) -> str:
    """`<APP_ENV>-<kind>-<row uid>` (BACKEND_SCHEMA §8); optional attempt suffix (P1 R12)."""
    key = f"{settings.app_env}-{kind}-{uid}"
    return key if attempt is None else f"{key}-{attempt}"


def _transient_status(status: int) -> bool:
    return status >= 500 or status == 429


def _error(resp: httpx.Response) -> PayPalError:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    return PayPalError(
        name=str(body.get("name") or body.get("error") or f"HTTP_{resp.status_code}"),
        message=str(body.get("message") or body.get("error_description") or "")[:300],
        debug_id=body.get("debug_id") or resp.headers.get("PayPal-Debug-Id"),
        details=body.get("details") or [],
        status=resp.status_code,
        transient=_transient_status(resp.status_code),
    )


class PayPal:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._transport = transport

    def _credentials(self) -> tuple[str, str]:
        s = self.settings
        missing = [
            name
            for name, value in (
                ("PAYPAL_CLIENT_ID", s.paypal_client_id),
                ("PAYPAL_CLIENT_SECRET", s.paypal_client_secret),
            )
            if not value
        ]
        if missing:
            raise PayPalNotConfiguredError(missing)
        assert s.paypal_client_id and s.paypal_client_secret
        return s.paypal_client_id, s.paypal_client_secret.get_secret_value()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.settings.paypal_base_url,
            timeout=httpx.Timeout(TIMEOUT_S),
            transport=self._transport,
        )

    async def _token(self, http: httpx.AsyncClient, *, force: bool = False) -> str:
        client_id, secret = self._credentials()
        key = (self.settings.paypal_base_url, client_id)
        cached = _tokens.get(key)
        if cached and not force and _now() < cached[1]:
            return cached[0]
        async with _token_lock(key):
            cached = _tokens.get(key)  # fetched by another call while we waited
            if cached and not force and _now() < cached[1]:
                return cached[0]
            return await self._fetch_token(http, key, client_id, secret)

    async def _fetch_token(
        self, http: httpx.AsyncClient, key: tuple[str, str], client_id: str, secret: str
    ) -> str:
        start = _now()
        resp = await http.post(
            "/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(client_id, secret),
            headers={"Accept": "application/json"},
        )
        self._log("POST", "/v1/oauth2/token", resp, start)
        if resp.status_code != 200:
            raise _error(resp)
        body = resp.json()
        _tokens[key] = (body["access_token"], _now() + int(body.get("expires_in", 0)) - 60)
        return body["access_token"]

    def _log(self, method: str, path: str, resp: httpx.Response, start: float) -> None:
        ms = int((_now() - start) * 1000)
        debug_id = resp.headers.get("PayPal-Debug-Id")
        level = logging.INFO if resp.status_code < 400 else logging.WARNING
        log.log(
            level,
            "paypal %s %s -> %s in %d ms debug_id=%s",
            method,
            path,
            resp.status_code,
            ms,
            debug_id,
        )

    async def ensure_token(self) -> None:
        """Fetch/cache the OAuth token now — capture calls this BEFORE taking the buyer lock,
        so the lock is never held while PayPal hands out a token (P2 R7)."""
        await _ensure(self)

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        request_id: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> Any:
        """Call PayPal and return the JSON body ({} when empty). Raises PayPalError.

        `timeout` (seconds, whole call) and `retries` override the defaults — capture uses
        10 s and no retries because it runs under the buyer lock (P2 D10, R7)."""
        method = method.upper()
        call_timeout = timeout if timeout is not None else TIMEOUT_S
        max_retries = len(BACKOFF_S) if retries is None else min(retries, len(BACKOFF_S))
        if method in WRITE_METHODS and not request_id:
            raise ValueError("every PayPal write needs a PayPal-Request-Id (invariant 4)")
        async with self._client() as http:
            token = await self._token(http)
            refreshed = False
            attempt = 0
            while True:
                hdrs = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                if request_id:
                    hdrs["PayPal-Request-Id"] = request_id
                hdrs.update(headers or {})
                start = _now()
                try:
                    resp = await http.request(
                        method, path, json=json, headers=hdrs, timeout=httpx.Timeout(call_timeout)
                    )
                except httpx.TimeoutException:
                    log.warning("paypal %s %s -> timeout after %.0f s", method, path, call_timeout)
                    failure = PayPalError(
                        "TIMEOUT", f"no answer in {call_timeout:.0f} s", None, [], None, True
                    )
                except httpx.TransportError as e:  # connection refused, DNS, reset: transient
                    log.warning("paypal %s %s -> network error %s", method, path, type(e).__name__)
                    failure = PayPalError("NETWORK_ERROR", type(e).__name__, None, [], None, True)
                else:
                    self._log(method, path, resp, start)
                    if resp.status_code == 401 and not refreshed:
                        refreshed = True
                        token = await self._token(http, force=True)
                        continue
                    if resp.status_code < 400:
                        return resp.json() if resp.content else {}
                    failure = _error(resp)
                if not failure.transient or attempt >= max_retries:
                    if failure.status is not None and failure.status >= 400:
                        log.warning(
                            "paypal %s %s failed: %s debug_id=%s",
                            method,
                            path,
                            failure.name,
                            failure.debug_id,
                        )
                    raise failure
                await _sleep(BACKOFF_S[attempt])
                attempt += 1


async def _ensure(pp: "PayPal") -> None:
    async with pp._client() as http:
        await pp._token(http)


def get_paypal() -> PayPal:
    """FastAPI dependency (D14); tests override it with a MockTransport-backed client."""
    return PayPal(get_settings())
