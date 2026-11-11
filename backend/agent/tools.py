"""Facts → the compact, record-id-tagged text inlined into the prompt (P4 D3, V3).

No tool loop: the model sees one buyer's records, each with its id, and answers in one
json_schema call. Records only — no settings, secrets, emails (R1, Y3).
"""

from backend.agent.facts import Facts


def usd(cents: int) -> str:
    """Two-decimal dollars for the prompt and code-written text ($1,420.00)."""
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{frac:02d}"


def facts_prompt(f: Facts) -> str:
    out = [
        f"Buyer: {f.buyer_name}. Today: {f.today.isoformat()}.",
        f"Owed now: {usd(f.owed_cents)}. Credit: {usd(f.credit_cents)}.",
        "",
        "MEMO LINES (id · description · price · status):",
    ]
    for ln in f.lines:
        if ln.status == "KEPT":
            st = f"KEPT, billed on invoice:{ln.invoice_no}"
        elif ln.status == "RETURNED":
            st = f"RETURNED on {ln.returned_on} (return:{ln.return_ref})"
        else:
            st = f"OUT (still with the buyer), due back {ln.due_back_on}"
        price = usd(ln.price_cents)
        out.append(f"- memo_line:{ln.sku} · memo:{ln.memo_no} · {ln.description} · {price} · {st}")
    out += ["", "INVOICES (id · memo · total · paid · due · issued · due date · overdue days):"]
    for i in f.invoices:
        out.append(
            f"- invoice:{i.invoice_no} · memo:{i.memo_no} · total {usd(i.total_cents)} · "
            f"paid {usd(i.paid_cents)} · due {usd(i.due_cents)} · issued {i.issued_on} · "
            f"due {i.due_on} · overdue {f.days_overdue(i)}"
        )
    out += ["", "PAYMENTS (id · amount · date · applied):"]
    if not f.payments:
        out.append("- none")
    for p in f.payments:
        applied = ", ".join(f"{no} {usd(c)}" for no, c in p.applied)
        out.append(f"- payment:{p.id} · {usd(p.amount_cents)} · {p.received_on} · {applied}")
    out += ["", "REQUESTS ALREADY SENT TO THE SUPPLIER (kind · invoice · sku · amount · status):"]
    if not f.proposals:
        out.append("- none")
    for pr in f.proposals:
        amount = usd(pr.amount_cents) if pr.amount_cents is not None else "-"
        out.append(
            f"- {pr.kind} · {pr.invoice_no or '-'} · {pr.sku or '-'} · {amount} · {pr.status}"
        )
    return "\n".join(out)
