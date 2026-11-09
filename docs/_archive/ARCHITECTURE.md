# Memo-to-Cash — Architecture

## Stack
- **Frontend:** Next.js (React, TypeScript), deployed on **Vercel**. PayPal JS SDK for the checkout buttons (`components=buttons`, `enable-funding=venmo,paylater`).
- **Backend:** **Python (FastAPI) as Vercel Python functions** under `/api/*`. All money logic lives here.
- **Database:** **Neon Postgres** (Vercel integration). Migrations with Alembic.
- **AI:** **Meta Llama API**, model `Llama-4-Maverick-17B-128E-Instruct-FP8`, via the OpenAI-compatible base `https://api.llama.com/compat/v1/`. Structured output with `response_format: json_schema`; tool calling for record lookups.
- **Secrets:** Vercel environment variables only (`PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_BASE_URL`, `PAYPAL_WEBHOOK_ID`, `LLM_API_KEY`, `DATABASE_URL`, `ADMIN_TOKEN`). `.env` is local only and git-ignored.

## Data model (Postgres; money in integer cents)
- `buyers(id, name, email, statement_token, created_at)`
- `memos(id, buyer_id, memo_no, sent_on, due_back_on)`
- `memo_lines(id, memo_id, sku, description, price_cents, status[OUT|RETURNED|KEPT], returned_on, return_ref)`
- `invoices(id, buyer_id, erp_invoice_no, paypal_invoice_id, total_cents, paid_cents, status, issued_on, payer_view_url)`
- `invoice_lines(id, invoice_id, memo_line_id, amount_cents)`
- `adjustments(id, invoice_id, kind[DEDUCTION|CREDIT], amount_cents, reason, status[PROPOSED|APPROVED|REJECTED], source_message_id)`
- `payments(id, buyer_id, paypal_order_id, paypal_capture_id UNIQUE, amount_cents, source[STATEMENT|PAYPAL_INVOICE_PAGE|RECORDED_CHEQUE], received_at)`
- `allocations(id, payment_id, invoice_id, amount_cents, paypal_recorded_at, paypal_record_error)`
- `messages(id, buyer_id, text, ai_result JSONB, created_at)`
- `proposals(id, buyer_id, kind, payload JSONB, evidence JSONB, status[PENDING|APPROVED|REJECTED|DONE], decided_by, decided_at)`
- `webhook_events(id, paypal_event_id UNIQUE, type, payload JSONB, processed_at)`
- `audit_log(id, at, actor[system|supplier|buyer|agent], action, ref, detail JSONB)`

## Flows
**A. Import + mirror.** Supplier uploads the ERP export (CSV: memos, lines, returns, invoices) → upsert → for each new invoice: `POST /v2/invoicing/invoices` (items = kept lines, `detail.reference` = memo no., `invoice_number` = ERP no., NET_30, partial payment on) → `POST /{id}/send` with `send_to_recipient:false` → store id + payer view URL. Idempotent on `erp_invoice_no`.

**B. Statement payment (the new capability).**
1. Buyer selects invoices/amount → `POST /api/statement/{token}/order` → server computes the split (deterministic, below) and creates `POST /v2/checkout/orders` (intent CAPTURE, one purchase unit, `custom_id` = our payment id, `description` lists invoice numbers).
2. Buyer approves in PayPal/Venmo/Pay Later → `POST /api/statement/{token}/capture` → `POST /v2/checkout/orders/{id}/capture`.
3. On `COMPLETED`: write payment + allocations in one DB transaction, then for each allocation `POST /v2/invoicing/invoices/{id}/payments` (`method: PAYPAL`, amount, `note: "Statement payment, capture <id>"`). Failures are stored and retried; never double-recorded (unique on payment_id+invoice_id).
- **Split rule:** only the invoices the buyer chose; oldest due first; never more than an invoice's balance; remainder → error before checkout (amount must be ≤ selected balance). Pure function, property-tested.

**C. Adjustments.** Approved deduction/credit → `GET` invoice → strip computed fields (`amount`, `due_amount`, `payments`, `refunds`, `links`, `status`, `gratuity`) → add negative line → `PUT`. (Tested Nov 8. To test: PUT on a PARTIALLY_PAID invoice.)

**D. Reminders.** Approved reminder → `POST /v2/invoicing/invoices/{id}/remind` (PayPal emails the buyer). Max 2/day enforced in code before calling.

**E. Webhooks** (`/api/paypal/webhook`): verify with `POST /v1/notifications/verify-webhook-signature`; dedupe on event id. `INVOICING.INVOICE.PAID` → re-fetch invoice; if a payment exists that we didn't record (paid on PayPal's own page) add it as `PAYPAL_INVOICE_PAGE`. `INVOICING.INVOICE.CANCELLED/REFUNDED`, `PAYMENT.CAPTURE.COMPLETED/REFUNDED` → update state.

**F. Agent.** See `AI.md`. The agent can only read records and create `proposals`. Only supplier-approved proposals reach flows C/D.

## Security and safety
- Statement link = long random token; read-only view of one buyer; rate-limited; no PII beyond buyer name.
- Supplier pages behind `ADMIN_TOKEN` (demo shows a "view as supplier" button with a fixed demo token, sandbox only).
- All amounts validated server-side; client never sends prices.
- Buyer text is untrusted: never executed as instructions; the model gets it as quoted data; output must match the JSON schema or it's discarded.
- LLM spend cap: max tokens per call, per-link and per-day call limits, model errors degrade to "We couldn't answer — sent to the supplier."
- Sandbox only. A guard refuses to start if `PAYPAL_BASE_URL` isn't the sandbox.

## Vercel constraints
- Functions are short-lived: no background workers. Retries run on the next request or a daily Vercel Cron.
- Keep every request under the function time limit; Llama calls have a timeout and a fallback.

## Open technical checks (before/while building)
1. PUT adjustment on a PARTIALLY_PAID invoice.
2. Venmo button rendering in sandbox (button visible; payment via QR may not complete in sandbox).
3. Webhook delivery to a Vercel preview URL.
4. Llama API latency for tool-calling answers within the function limit.
