"""Money is integer cents (invariant 1). Parse and format without floats."""

import re

_DOLLARS = re.compile(r"^\d+(\.\d{1,2})?$")


def to_cents(text: str) -> int:
    """'1150' / '1150.5' / '1150.50' → 115050-style cents. Max 2 decimals, no sign, no float."""
    if not isinstance(text, str):
        raise TypeError("to_cents takes a string, never a float or int")
    s = text.strip()
    if not _DOLLARS.match(s):
        raise ValueError(f"not a dollar amount: {text!r}")
    whole, _, frac = s.partition(".")
    return int(whole) * 100 + int(frac.ljust(2, "0") or "0")


def fmt_paypal(cents: int) -> str:
    """115000 → '1150.00' (PayPal Money value). Negative allowed for adjustment lines."""
    if not isinstance(cents, int) or isinstance(cents, bool):
        raise TypeError("fmt_paypal takes integer cents")
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole}.{frac:02d}"
