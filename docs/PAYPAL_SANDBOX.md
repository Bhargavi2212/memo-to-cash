# PayPal behaviour we proved in the sandbox

Every PayPal behaviour Memo-to-Cash depends on was tested against the real PayPal sandbox before
we built on it. Each result names the script that proves it (`scripts/sandbox/`, re-runnable)
and the date. Redacted responses are saved in `tests/fixtures/paypal/` and drive the tests.

## Invoicing v2

| Behaviour | Result | Proof |
|---|---|---|
| Create with past `invoice_date` and a past due date (`DUE_ON_DATE_SPECIFIED`) | Kept as sent; status UNPAID. PayPal's due date equals ours. | `p1_open_checks.py`, Nov 9 |
| Create with `Prefer: return=representation` | Returns the full invoice. | `p1_open_checks.py`, Nov 9 |
| Send | Returns the payer link (`rel: payer-view`) — no extra GET needed. | `p1_open_checks.py`, Nov 9 |
| `PayPal-Request-Id` on create | **Not honoured** — a repeat fails as a duplicate number. We resume by searching our unique invoice number and adopting the invoice. | `p1_open_checks.py`, Nov 9 |
| Duplicate invoice number | 422 `UNPROCESSABLE_ENTITY` with `details[].issue = DUPLICATE_INVOICE_NUMBER` — we match the issue, not the name. | `p1_open_checks.py`, Nov 9 |
| Record a payment | Returns `{payment_id}`; the note (with our `[MTC:<uid>]` token) is visible in the invoice's transactions. $0.05 can be recorded. | `p1_open_checks.py`, Nov 9 |
| Record a payment with a checkout capture id as `payment_id` | `RESOURCE_NOT_FOUND` — so we don't send it. Recorded payments show `MARKED_AS_PAID` / `PARTIALLY_PAID`, not `PAID`. | P1 / P2 sandbox runs |
| Record more than is due | `PAYMENT_AMOUNT_GREATER_THAN_AMOUNT_DUE` — our split prevents it; if a buyer also paid on PayPal's page, the reconcile turns our duplicate into credit. | Sandbox test, P2 |
| Update (GET → strip computed fields → add a negative line → PUT) | Works, including on a PARTIALLY_PAID invoice. PayPal's due drops by exactly the line; afterwards the sandbox reports status `SENT`. Sending stale totals → `calculation_error`. | `p4_approve_check.py`, Nov 10 |
| PUT that brings a part-paid invoice to exactly $0.00 due | **Accepted** ($3,500 invoice, $1,000 recorded, −$2,500 line → due $0.00, `MARKED_AS_PAID`). The "take what's due, credit the rest" option is built on this. | `p3b_probes.py split`, Nov 11 |
| Remind | `POST /invoices/{id}/remind` with `{subject, note, send_to_invoicer: false, send_to_recipient: true}` → empty 2xx (~0.8 s). **The sandbox accepted a 3rd reminder the same day** — PayPal's 2-a-day limit isn't enforced there, so our own count enforces it. | `p3b_probes.py remind`, Nov 11 |

## Orders v2 + JS SDK

| Behaviour | Result | Proof |
|---|---|---|
| One checkout order for several invoices | Works: one order with the split as items, captured once; PayPal offered Pay Later automatically. | `p2_smoke.py`, Nov 10 |
| Capture with `Prefer: return=representation` | Returns the capture (`id`, status `COMPLETED`, amount, `custom_id`); `NO_SHIPPING` removes the address step. | `p2_smoke.py`, Nov 10 |
| An approved order we decide not to capture | Can't be voided — we simply don't capture (it expires) and mark our payment VOIDED. | P2 |
| Items on the order | Stored, but PayPal's checkout window shows only the total — our own preview and receipt show the split. | P2 smoke + live run, Nov 10 |

## Webhooks

| Behaviour | Result | Proof |
|---|---|---|
| Real delivery to the api on Render + signature verification | `verify-webhook-signature` (raw body spliced in) → SUCCESS in 220 ms; the payment made on PayPal's own invoice page was in our ledger about 1 s later. | Live run on Render, Nov 12 |
| Webhooks Simulator events | Can't be verified by postback (PayPal's docs) — so the check used a real payment. | PayPal docs |

## Not proven
- A Venmo payment completing in the sandbox (the button renders; completing needs the Venmo app).
- The split appearing as items inside PayPal's checkout window (it doesn't — see above).
