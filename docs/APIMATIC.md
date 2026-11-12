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
| 4 | 2026-11-10 | P2 | `endpoint_search(create_order)` | `CreateOrder(body: OrderRequest, paypal_request_id, prefer)`; `prefer=return=representation` returns the full order; Request-Id kept 6 h, mandatory for single-step orders with a payment source; errors 400/401/422 as `ErrorException`. Also returned `CaptureOrder(id, paypal_request_id, prefer, body)` with 400/401/403/404/422/500. | `orders.create` / `orders.capture` send `PayPal-Request-Id` + `Prefer: return=representation` · step 4 commit |
| 5 | 2026-11-10 | P2 | `model_search(PurchaseUnitRequest)` (+ `OrderRequest`, `ItemRequest`) | `items[] {name, quantity (whole number string), unit_amount}`; `amount.breakdown` must satisfy amount = item_total + …; `custom_id` (not shown to payer, in reports); `invoice_id` **unique per merchant by default**; `description` truncated at 127. | One item per invoice (the split shows in PayPal's window), `item_total` = amount, `custom_id` = payment uid, `invoice_id` = `<env>-stmt-<uid>` so PayPal refuses a second charge · `build_order_body` |
| 6 | 2026-11-10 | P2 | `model_search(PaypalWalletExperienceContext)`, `VenmoWalletExperienceContext`, `PaypalWalletContextShippingPreference`, `PaypalExperienceUserAction` | PayPal: `brand_name, shipping_preference (GET_FROM_FILE/NO_SHIPPING/SET_PROVIDED_ADDRESS), user_action (CONTINUE/PAY_NOW), landing_page, …`; Venmo: `brand_name, shipping_preference, user_action`. | `NO_SHIPPING` + `PAY_NOW` + `brand_name` in `payment_source.paypal|venmo.experience_context` (R3 mapping) |
| 7 | 2026-11-10 | P2 | `model_search(OrderApplicationContext)` | **`application_context` is DEPRECATED** — its fields now live in `payment_source.<source>.experience_context`. | experience_context for PayPal/Pay Later/Venmo; deprecated `application_context` kept **only** for the guest-card path (no payment_source) — confirmed by the step 5 smoke |
| 8 | 2026-11-10 | P2 | `model_search(CaptureStatus)`, `OrdersCapture`, `ErrorDetails` | Capture statuses `COMPLETED, DECLINED, PARTIALLY_REFUNDED, PENDING, REFUNDED, FAILED`; capture has `id, status, status_details, amount, custom_id, invoice_id, seller_receivable_breakdown (not when pending)`; error `details[] {field, value, location, issue, description}` — `issue` is the fine-grained code. **`ask` for the list of capture `issue` names failed twice** (tool error) → names taken from PayPal's Orders error docs, checked live in the step 5 smoke. | `first_capture`, D6 PENDING/DECLINED/FAILED handling, `classify()` by `details[].issue` · step 4 commit |
| | | P2 | (smoke results: capture shape, NO_SHIPPING, items shown) | | `scripts/sandbox/p2_smoke.py` |
| 9 | 2026-11-11 | P3b | `fetch_api(paypal)` + `endpoint_search("verify webhook signature notifications")` | **Not covered:** the context is the PayPal Server SDK (Orders, Payments, Vault, Transaction Search, Subscriptions) — no Notifications/Webhooks API; the search returned only ConfirmOrder, UpdateOrderTracking, ReviseSubscription, SearchTransactions, ListSubscriptions. Verification built from PayPal's REST docs (`POST /v1/notifications/verify-webhook-signature`, five transmission headers + `webhook_id` + raw `webhook_event`), like Invoicing in P1 D23 | `backend/paypal/webhooks.py` |

## Where the plugin and our sandbox tests disagreed
_None yet. Our sandbox results win if they ever do (e.g. `payment_id` on record-payment is rejected for checkout captures — an Invoicing fact, outside the plugin's context)._

## Commits that used it
`git log --grep "\[apimatic\]" --oneline`
