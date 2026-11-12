"""PayPal webhook signature verification (FR-7; P3b B1, R9, §9 F1; TRD §7).

`POST /v1/notifications/verify-webhook-signature` with the five transmission headers, our
`PAYPAL_WEBHOOK_ID` and the event as `webhook_event`. The event goes in as the **raw request
bytes**, spliced into the JSON — re-serialising a parsed dict can change numbers or key order
and break PayPal's check. Not in the APIMatic ContextMatic context (Notifications API isn't part
of the PayPal Server SDK — docs/APIMATIC.md), built from PayPal's REST docs.
Simulator (mock) events can't be verified this way — PayPal says so; only real deliveries pass.
"""

import json
from collections.abc import Mapping

from backend.paypal.client import PayPal, request_id

VERIFY_PATH = "/v1/notifications/verify-webhook-signature"
# PayPal header → field of the verify request
HEADERS = {
    "paypal-auth-algo": "auth_algo",
    "paypal-cert-url": "cert_url",
    "paypal-transmission-id": "transmission_id",
    "paypal-transmission-sig": "transmission_sig",
    "paypal-transmission-time": "transmission_time",
}
VERIFY_TIMEOUT_S = 5.0


def transmission(headers: Mapping[str, str]) -> dict[str, str] | None:
    """The five transmission fields, or None if any is missing (→ 400, PayPal not asked)."""
    lower = {k.lower(): v for k, v in headers.items()}
    out = {field: lower.get(h, "") for h, field in HEADERS.items()}
    return out if all(out.values()) else None


def verify_body(fields: dict[str, str], webhook_id: str, raw: bytes) -> bytes:
    """`{...fields, "webhook_id": …, "webhook_event": <raw bytes>}` with the event spliced."""
    head = json.dumps({**fields, "webhook_id": webhook_id})[:-1]
    return head.encode() + b', "webhook_event": ' + raw + b"}"


async def verify(pp: PayPal, fields: dict[str, str], webhook_id: str, raw: bytes) -> bool:
    """True only for `verification_status == SUCCESS`. Raises PayPalError (transient → 503)."""
    body = await pp.request(
        "POST",
        VERIFY_PATH,
        content=verify_body(fields, webhook_id, raw),
        request_id=request_id(pp.settings, "whv", fields["transmission_id"]),
        timeout=VERIFY_TIMEOUT_S,
        retries=1,
    )
    return body.get("verification_status") == "SUCCESS"
