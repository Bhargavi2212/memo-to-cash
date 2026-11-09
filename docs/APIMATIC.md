# APIMatic Context Plugin for PayPal — how we used it

We built Memo-to-Cash's PayPal integration with Claude Code + the **APIMatic Context Plugin for PayPal**, which gives the coding agent PayPal API context (endpoints, fields, errors) instead of guessing from memory.

## Setup
- Installed in Claude Code on: _(date)_ — steps from APIMatic's resource page. Config committed: _(path, if the plugin uses a project-level config file)_.
- Screenshot of Claude Code using the plugin: `docs/img/apimatic-*.png`

## Usage log (one row per PayPal call built or changed)
| Date | Phase | What we asked via the plugin | What it gave us (fields/limits/errors) | Code + commit |
|---|---|---|---|---|
| | P1 | Create invoice body: `invoice_date`, `payment_term.due_date`, partial payment config | | `backend/paypal/invoices.py` · commit |
| | P1 | Send invoice + where the payer URL lives | | |
| | P1 | Record payment fields and allowed `method` values | | |
| | P1 | Update (PUT) invoice: which fields are read-only | | |
| | P2 | Create order + capture, `custom_id`, `PayPal-Request-Id` | | `backend/paypal/orders.py` |
| | P3 | Webhook verify-signature body; `INVOICING.INVOICE.PAID` payload | | `backend/paypal/webhooks.py` |
| | P3 | Invoice remind endpoint + limits | | |

## Where the plugin and our sandbox tests disagreed
_(record any case; our sandbox results win — e.g. `payment_id` on record-payment is rejected for checkout captures)_

## Commits that used it
`git log --grep "\[apimatic\]" --oneline`
