"""Facts → the compact, record-id-tagged text inlined into the prompt (P4 D3, V3).

No tool loop: the model sees one buyer's records, each with its id, and answers in one
json_schema call. Records only — no settings, secrets, emails (R1, Y3).
"""

from datetime import date

from backend.agent.facts import Facts


def usd(cents: int) -> str:
    """Two-decimal dollars for the prompt and code-written text ($1,420.00)."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{frac:02d}"


def record_ids(f: Facts) -> set[str]:
    """Every record id the prompt shows — what an answer may cite (validators, step 3)."""
    ids = {f"memo:{x.memo_no}" for x in f.memos}
    for ln in f.lines:
        ids.add(f"memo_line:{ln.sku}")
        if ln.return_ref:
            ids.add(f"return:{ln.return_ref}")
    ids |= {f"invoice:{i.invoice_no}" for i in f.invoices}
    ids |= {f"payment:{p.id}" for p in f.payments}
    ids |= {f"proposal:{p.id}" for p in f.proposals if p.status == "PENDING"}  # Z8
    return ids


def unknown_records(f: Facts, cited: list[str]) -> list[str]:
    """Cited ids that aren't this buyer's records (empty = every cited record exists)."""
    known = {r.lower() for r in record_ids(f)}
    return [c for c in cited if c.strip().lower() not in known]


def day(d: date) -> str:
    """Dates the buyer reads ("Oct 3, 2026"): the model copies them as written, never ISO."""
    return f"{d:%b} {d.day}, {d.year}"


def _days(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def _ago(f: Facts, d: date) -> str:
    n = (f.today - d).days
    return "today" if n == 0 else f"{_days(n)} ago"


def _rel(f: Facts, d: date, late: str = "late") -> str:
    """Day counts are computed here, not by the model — the validator allows exactly these."""
    n = (d - f.today).days
    if n == 0:
        return "today"
    return f"in {_days(n)}" if n > 0 else f"{_days(-n)} {late}"


def facts_prompt(f: Facts) -> str:
    out = [
        f"Buyer: {f.buyer_name}. Today: {day(f.today)}.",
        f"Owed now: {usd(f.owed_cents)}. Credit: {usd(f.credit_cents)}.",
        "",
        "MEMO LINES (id · description · price · status):",
    ]
    for ln in f.lines:
        if ln.status == "KEPT":
            st = f"KEPT, billed on invoice:{ln.invoice_no}"
        elif ln.status == "RETURNED" and ln.returned_on:
            when = f"{day(ln.returned_on)}, {_ago(f, ln.returned_on)}"
            st = f"RETURNED on {when} (return:{ln.return_ref})"
        elif ln.due_back_on:
            when = f"{day(ln.due_back_on)}, {_rel(f, ln.due_back_on)}"
            st = f"OUT (still with the buyer), due back {when}"
        else:
            st = ln.status
        price = usd(ln.price_cents)
        out.append(f"- memo_line:{ln.sku} · memo:{ln.memo_no} · {ln.description} · {price} · {st}")
    out += ["", "INVOICES (id · memo · total · paid · due · issued · due date):"]
    for i in f.invoices:
        when = "paid in full" if i.due_cents <= 0 else _rel(f, i.due_on, late="overdue")
        out.append(
            f"- invoice:{i.invoice_no} · memo:{i.memo_no} · total {usd(i.total_cents)} · "
            f"paid {usd(i.paid_cents)} · due {usd(i.due_cents)} · "
            f"issued {day(i.issued_on)}, {_ago(f, i.issued_on)} · due date {day(i.due_on)}, {when}"
        )
    out += ["", "PAYMENTS (id · amount · date · applied):"]
    if not f.payments:
        out.append("- none")
    for p in f.payments:
        applied = ", ".join(f"{no} {usd(c)}" for no, c in p.applied)
        out.append(f"- payment:{p.id} · {usd(p.amount_cents)} · {day(p.received_on)} · {applied}")
    out += [
        "",
        "REQUESTS ALREADY SENT TO THE SUPPLIER (id · kind · invoice · sku · amount · status):",
    ]
    if not f.proposals:
        out.append("- none")
    for pr in f.proposals:
        amount = usd(pr.amount_cents) if pr.amount_cents is not None else "-"
        refs = f"{pr.invoice_no or '-'} · {pr.sku or '-'}"
        out.append(f"- proposal:{pr.id} · {pr.kind} · {refs} · {amount} · {pr.status}")
    return "\n".join(out)
