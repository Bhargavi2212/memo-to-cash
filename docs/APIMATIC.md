# APIMatic Context Plugin for PayPal — how we used it

We built Memo-to-Cash's PayPal integration with Claude Code + the **APIMatic Context Plugin for PayPal** (ContextMatic), which gives the coding agent PayPal API context (endpoints, models, errors) instead of guessing from memory. Every row below quotes a real lookup made during a coding session.

## Setup
- Installed in Claude Code on **2026-11-09** (P1): `/plugin marketplace add apimatic/context-matic` → `/plugin install context-matic@apimatic` (v0.1.0, user scope) → `/reload-plugins` → signed in to the `context-matic` MCP server (GitHub) via `/mcp`. No project-level config file; nothing secret stored.
- Tools used: `fetch_api` (API key `paypal`), `ask`, `endpoint_search`, `model_search`; language `python`.
- Screenshot of Claude Code using the plugin: `docs/img/apimatic-1.png`.

## Coverage — what the plugin's PayPal context includes (found in P1)
ContextMatic's PayPal context is the **PayPal Server SDK**: Orders, Payments, Vault (US), Transaction Search, Subscriptions. It does **not** include **Invoicing v2** (`/v2/invoicing/invoices`: create, send, get, search, record payment, update, cancel) — the plugin said so itself (row 1).
So in Memo-to-Cash:
- **Built with the plugin** (`[apimatic]` commits): the PayPal client — error format, OAuth token fields, `PayPal-Request-Id` (P1); Orders create + capture for "pay many invoices in one payment" (P2).
- **Built without it**: every Invoicing call, from our sandbox-proven facts (`CLAUDE.md §4`) and PayPal's REST docs; invoice field paths confirmed by a real sandbox run (P1 open checks). Those commits carry no `[apimatic]` tag.
- Feedback for APIMatic: adding Invoicing v2 would cover the B2B half of this project (invoices are what suppliers send; Orders is how we collect).

## Usage log (one row per real lookup)
| # | Date | Phase | What we asked via the plugin | What it gave us | Used in |
|---|---|---|---|---|---|
| 1 | 2026-11-09 | P1 | `fetch_api(paypal)` + `ask`: does the context cover Invoicing v2 (create/send/record/search), and which fields hold the invoicer business name and bill-to name? | API = "PayPal Server SDK" (Orders, Payments, Vault, Transaction Search, Subscriptions). Answer: "does **not** cover the Invoicing v2 API … Invoicing is not listed." | Coverage note above; P1 D23 (spec changed: invoice field paths confirmed by the sandbox run instead) |
| 2 | 2026-11-09 | P1 | `ask`: OAuth2 client-credentials token, standard error response, how `PayPal-Request-Id` is used on create/capture order | Error model `ErrorException {name, message, debug_id, details[], links[]}`; token model `OAuthToken {access_token, token_type, expires_in, scope, …}` (token endpoint not in its docs — ours is proven in P0 health). | `backend/paypal/client.py` (`PayPalError` fields, token cache by `expires_in`) · step 2 commit |
| 3 | 2026-11-09 | P1 | (same lookup) `PayPal-Request-Id` on `CreateOrder` / `CaptureOrder` | **PayPal stores request keys for 6 hours** (extendable to 72 h only via an account manager); mandatory for single-step create-order with a payment source. | **Design change:** after 6 h a reused Request-Id no longer prevents duplicates, and our sweep can retry later than that → recovery relies on the `[MTC:<uid>]` note token and capture-id/order-status checks (TRD §6b-5) |
| | | P2 | Create order + capture, `custom_id`, `PayPal-Request-Id` | | `backend/paypal/orders.py` |
| | | P3 | Webhook verify-signature body; `INVOICING.INVOICE.PAID` payload (check coverage first) | | `backend/paypal/webhooks.py` |

## Where the plugin and our sandbox tests disagreed
_None yet. Our sandbox results win if they ever do (e.g. `payment_id` on record-payment is rejected for checkout captures — an Invoicing fact, outside the plugin's context)._

## Commits that used it
`git log --grep "\[apimatic\]" --oneline`
