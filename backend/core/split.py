"""The one split function (invariant 2, P2 D1). Pure: no DB, no clock, no PayPal.

`split_payment(amount_cents, invoices, selected_ids)` → `[(invoice_id, cents), ...]`
- only selected invoices with a balance;
- oldest `due_on` first; ties → `issued_on`, then `id`;
- never above an invoice's balance; any positive cent amount; sum == amount;
- errors are `SplitError(code)` (a ValueError): AMOUNT_NOT_POSITIVE, NOTHING_SELECTED,
  AMOUNT_EXCEEDS_SELECTED.

`resolve_request(...)` applies the request defaults from BACKEND_SCHEMA §8 before splitting.
"""

from collections.abc import Iterable, Sequence
from datetime import date
from typing import NamedTuple


class OpenInvoice(NamedTuple):
    id: int
    due_on: date
    issued_on: date
    balance_cents: int  # total - paid


class SplitError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _order(inv: OpenInvoice) -> tuple[date, date, int]:
    return (inv.due_on, inv.issued_on, inv.id)


def _check_amount(amount_cents: int) -> None:
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool):
        raise TypeError("amount_cents must be integer cents")
    if amount_cents <= 0:
        raise SplitError("AMOUNT_NOT_POSITIVE", "Enter an amount above $0.00.")


def split_payment(
    amount_cents: int, invoices: Iterable[OpenInvoice], selected_ids: Iterable[int]
) -> list[tuple[int, int]]:
    _check_amount(amount_cents)
    selected = set(selected_ids)
    chosen = sorted((i for i in invoices if i.id in selected and i.balance_cents > 0), key=_order)
    if not chosen:
        raise SplitError("NOTHING_SELECTED", "Choose at least one invoice with money owed.")
    available = sum(i.balance_cents for i in chosen)
    if amount_cents > available:
        raise SplitError(
            "AMOUNT_EXCEEDS_SELECTED",
            "That's more than the selected invoices owe — lower the amount or select more.",
        )
    out: list[tuple[int, int]] = []
    left = amount_cents
    for inv in chosen:
        if left == 0:
            break
        take = min(left, inv.balance_cents)
        out.append((inv.id, take))
        left -= take
    return out


def resolve_request(
    invoices: Sequence[OpenInvoice],
    invoice_ids: Sequence[int] | None,
    amount_cents: int | None,
) -> tuple[int, list[int]]:
    """BACKEND_SCHEMA §8 defaults → (amount_cents, selected_ids), validated.

    - neither given → all open invoices, full balance;
    - ids only → full balance of those invoices;
    - amount only → all open invoices (oldest due first);
    - both → that amount across those invoices.
    """
    open_ids = [i.id for i in sorted(invoices, key=_order) if i.balance_cents > 0]
    if invoice_ids is None:
        selected = open_ids
    else:
        selected = list(dict.fromkeys(invoice_ids))  # keep order, drop repeats
        bad = [i for i in selected if i not in set(open_ids)]
        if bad:
            raise SplitError(
                "INVOICE_NOT_OPEN", "One of the selected invoices is already paid or not yours."
            )
    if not selected:
        raise SplitError("NOTHING_SELECTED", "Choose at least one invoice with money owed.")
    if amount_cents is None:
        chosen = set(selected)
        amount_cents = sum(i.balance_cents for i in invoices if i.id in chosen)
    _check_amount(amount_cents)
    return amount_cents, selected
