# Memo-to-Cash — Technical Requirements (TRD)

Status: v1 (Nov 9, 2026). Read with `PRD.md`, `APP_FLOW.md`, `BACKEND_SCHEMA.md`.

## 1. Stack (fixed)
| Layer | Choice | Why |
|---|---|---|
| Frontend | Next.js (latest stable from `create-next-app` at P0, App Router, TypeScript), Tailwind CSS v4 — Render web service | Fast mobile pages; `/api/*` forwarded at runtime by a proxy route (`app/api/[...path]/route.ts`) to the API service (same origin) |
| Payments UI | PayPal JS SDK (`components=buttons`, `enable-funding=venmo,paylater`) | Official buttons for PayPal, Venmo, Pay Later |
| Backend | Python 3.12, FastAPI (`backend/main.py`, uvicorn) — Render web service; plus a Render **background worker** (`backend/worker.py`, P3) | Money logic in Python; always-on server, real background jobs |
| DB | Neon Postgres (neon.tech; branches `main`=prod, `dev`, `test`), SQLAlchemy 2 + Alembic | Free, doesn't expire before judging, branches for dev/test |
| AI | Meta Model API, `muse-ember-1.0` (reasoning effort `low`), base `https://api.meta.ai/v1` (OpenAI client); backup `muse-spark-1.3`; final model chosen by the P4 eval | JSON-schema output + tool calling; ~1.1 s per call in the P0 test |
| Hosting | **Render** (sponsor) via `render.yaml` Blueprint: web + api (+ worker from P3), plan `0.5c-512mb` (0.5 CPU / 512 MB, $7/mo each; formerly "Starter") | Always on (no sleep), paid by hackathon credits through Dec 15; eligible for Render's sponsor prize |
| Dev tooling | **APIMatic Context Plugin for PayPal** in Claude Code (sponsor tool) | PayPal API context while building; qualifies for the APIMatic sponsor prize |
| CI | GitHub Actions: pytest, ruff, `tsc --noEmit`, eslint | Green before every deploy |

## 2. Repo layout
The file structure lives in **`PROJECT.md` → File structure** (single source; update it there when files are added or moved).

## 3. Functional requirements
| ID | Requirement | Acceptance |
|---|---|---|
| FR-1 | Import ERP export (CSV) of memos, lines, returns, invoices; idempotent | Re-import of same file changes nothing; row errors reported per line |
| FR-2 | Mirror each ERP invoice to a PayPal invoice (kept lines as items, memo no. as `detail.reference`, `paypal_invoice_number` as `invoice_number`, **`invoice_date` + `DUE_ON_DATE_SPECIFIED` due date equal to ours**, partial payments on), send with `send_to_recipient:false` | PayPal invoice exists with the same due date as ours; `paypal_status` stored (e.g. UNPAID); payer URL stored; no duplicate on retry |
| FR-3 | Buyer statement by secret token: memos → lines (OUT/RETURNED/KEPT) → invoices, totals, ages | Totals equal sum of invoice balances to the cent |
| FR-4 | Buyer selects invoices or an amount; server computes split before checkout | Split shown before paying; amount > selected balance rejected |
| FR-5 | One PayPal order for the statement payment; capture on approval | Order amount = split total; capture COMPLETED stored once (unique capture id) |
| FR-6 | Record each allocation on its PayPal invoice (`method: PAYPAL`, note with capture id) | PayPal due amounts match our balances; failures stored + retried, never doubled |
| FR-7 | Webhooks: `INVOICING.INVOICE.PAID/CANCELLED/REFUNDED`, `CHECKOUT.ORDER.APPROVED`, `PAYMENT.CAPTURE.COMPLETED/REFUNDED`; signature verified; deduped | Paying one invoice on PayPal's own page updates the statement |
| FR-8 | "Ask about this statement": grounded answers with cited records | Every cited id exists and belongs to this buyer, else safe fallback |
| FR-9 | Buyer message → proposal (label, fields, evidence, action) | Proposal visible to supplier; no state change before approval |
| FR-10 | Supplier approves/edits/rejects proposals; approved adjustment → PayPal invoice PUT; approved reminder → PayPal remind | PayPal invoice reflects change; reminders ≤ 2/day/invoice |
| FR-11 | Supplier dashboard: per-buyer owed/overdue, payments with splits, audit trail | Numbers match DB; every money event has an audit row |
| FR-12 | Demo (backend in P1, see `docs/phases/P1.md`): **each visitor gets their own demo copy** (`POST /demo/start` creates a fresh demo buyer + PayPal invoices with a unique suffix, sets a demo cookie); judge path on `/`; sandbox buyer hint | Two judges at the same time never see each other's payments; fresh judge pays and sees supplier side in < 3 minutes |
| FR-13 | Eval: `python -m eval.run_eval` prints agent vs baseline metrics | Numbers in README come from this script |
| FR-14 | Supplier records an off-PayPal payment (cheque/wire/other) for chosen invoices or an amount | Same split rule; recorded on each PayPal invoice with the matching `method`; appears on statement |
| FR-15 | Statement link delivery: copy-link button on supplier buyer row; link written into PayPal invoice `detail.note` | Link opens the buyer's statement; note visible on PayPal's invoice page |

## 4. Non-functional requirements
- **Ledger rule (one source of truth):** *our* ledger (payments + allocations) decides what a buyer owes. PayPal invoices mirror it. `invoices.paid_cents` = sum of that invoice's allocations. PayPal status is fetched and shown, and a mismatch is flagged on the supplier screen — never silently overwritten.
- **Allocations:** any positive cent amount, never above an invoice's balance. Full rules (defaults, re-split at capture, request-id keys) in `BACKEND_SCHEMA.md §8`.
- **Money correctness:** integer cents everywhere; split is a pure function with property tests (sum of allocations = payment; no allocation > balance; only selected invoices; oldest due first).
- **Idempotency:** unique keys on `paypal_invoice_number`, `(buyer_id, erp_invoice_no)`, `(buyer_id, memo_no)`, `paypal_capture_id`, `(payment_id, invoice_id)`, `paypal_transaction_id`, `paypal_event_id`; `PayPal-Request-Id` on every PayPal write, keyed by env + row uid (`BACKEND_SCHEMA.md §8`).
- **Safety:** sandbox guard (refuse to boot if base URL isn't sandbox); no money change without supplier approval; buyer text treated as data.
- **Security:** statement token ≥ 32 random bytes (URL-safe); supplier routes require `ADMIN_TOKEN`, **except** in `DEMO_MODE` where a signed demo cookie gives supplier access **only to that visitor's own demo buyer** (never to other data); secrets only in Render env (and local `.env`); no secrets in logs; CORS same-origin.
- **Performance:** statement page < 1.5 s on 4G for 20 invoices; agent answer < 8 s or fallback; every request answers within 10 s (PayPal/LLM calls have their own timeouts).
- **Reliability:** PayPal calls retry 3× with backoff on 5xx/429; partial failures leave a visible, retryable state.
- **Observability:** structured logs with request id + PayPal debug_id; audit_log for every money and agent event.
- **Accessibility:** WCAG AA contrast; status shown as text, not colour only; keyboard usable; mobile-first.
- **Cost guard:** LLM max 800 output tokens; ≤ 20 agent calls per statement link per day; ≤ 500 per day total; fallbacks on error.

## 5. PayPal integration spec
| Use | Call | Notes |
|---|---|---|
| Auth | `POST /v1/oauth2/token` | Cache token until `expires_in - 60s` |
| Create invoice | `POST /v2/invoicing/invoices` | Body per `BACKEND_SCHEMA.md §4`; `PayPal-Request-Id` per `BACKEND_SCHEMA.md §8` (`<env>-inv-<uid>`) |
| Send invoice | `POST /v2/invoicing/invoices/{id}/send` `{"send_to_recipient": false}` | Then `GET` the invoice: payer URL is `detail.metadata.recipient_view_url` |
| Get invoice | `GET /v2/invoicing/invoices/{id}` | Source of truth for PayPal status/due |
| Record payment | `POST /v2/invoicing/invoices/{id}/payments` `{method:"PAYPAL", payment_date, amount, note}` | Do **not** send `payment_id` (capture IDs rejected: RESOURCE_NOT_FOUND, tested) |
| Adjust | `GET` → strip `amount,due_amount,payments,refunds,links,status,gratuity` → add negative item → `PUT /v2/invoicing/invoices/{id}` | Tested on unpaid invoice; partially-paid case = open check |
| Remind | `POST /v2/invoicing/invoices/{id}/remind` | Max 2/day (PayPal limit) |
| Search | `POST /v2/invoicing/search-invoices` by `invoice_number` | Mirror resume: only on a retry (`mirror_attempts > 0`) or after `DUPLICATE_INVOICE_NUMBER` → adopt the found invoice (P1 D4); also reconciliation/backfill |
| Create order | `POST /v2/checkout/orders` intent CAPTURE, 1 purchase unit, `custom_id` = our payment `uid` | Amount from server split only |
| Capture | `POST /v2/checkout/orders/{id}/capture` | Store capture id; idempotent |
| Webhook verify | `POST /v1/notifications/verify-webhook-signature` | Needs `PAYPAL_WEBHOOK_ID` |
Known limits: one invoice per PayPal invoice page; recorded payments show `MARKED_AS_PAID`/`PARTIALLY_PAID`; Venmo needs the Venmo app (desktop = QR); Pay Later is consumer credit (US: Pay in 4 $30–$1,500, Pay Monthly $199–$10,000).

## 6. AI spec
**Principle:** the model reads and proposes; deterministic code validates; the supplier decides.
- Client: OpenAI SDK pointed at `https://api.meta.ai/v1`; model `LLM_MODEL` (default `muse-ember-1.0`), `reasoning_effort` = `LLM_REASONING_EFFORT` (default `low`); `temperature 0.1`; `response_format: {type: json_schema}`; timeout 8 s; 1 retry. Never a `-contributor` model.
- Muse models reason before answering. A reply cut off by the token limit (`finish_reason="length"`) or not valid JSON is a failure → safe fallback + proposal, never a partial answer.
- **Tool calling + JSON schema in one call works** (open check 7, P0: 3/3 runs on `muse-ember-1.0` and `muse-spark-1.3`). Tool loop and final answer use the same call shape: `tools` + `response_format: json_schema`.
- **Job A — Answer (FR-8):** tools `get_statement`, `find_memo_line(sku)`, `get_return_record(sku)`, `get_invoice(no)` (all scoped to the token's buyer). Output `AnswerResult` (see schema doc). Validator: cited ids exist + belong to buyer; every money figure in `answer` must be in the **allowed set** = record values ∪ totals computed by code (buyer owed, each invoice's due/paid/total, each memo's kept/returned/out sums); else fallback "I'll ask the supplier" + proposal.
- **Job B — Triage (FR-9):** output `TriageResult` (label ∈ QUESTION, RETURN_CLAIM, DEDUCTION_REQUEST, PAYMENT_PROMISE, DISPUTE, OTHER; fields sku, amount_cents, date, invoice_no, reason). Validator: SKU on buyer's memos; deduction ≤ line price **and** ≤ invoice due (`BACKEND_SCHEMA.md §8`); return claim compared to records → evidence `MATCH | NO_RECORD | ALREADY_CREDITED`.
- **Job C — Reminder draft:** output `ReminderDraft {text, send_after}`; supplier approval required; 2/day enforced in code.
- **Prompt-injection:** buyer text wrapped as quoted data with an explicit instruction that it is not instructions; schema-only outputs; tools are read-only.
- **Eval (FR-13):** `eval/messages.jsonl` (target 30; real redacted messages first). Baseline = keyword rules. Metrics: label accuracy, field exact-match, correct-action rate, unsafe-action rate (target 0), grounding rate for answers.

## 6b. Webhook dedupe rule (race-safe)
1. **Before** calling record-payment, set the allocation's `record_state = RECORDING` and commit. The note we send always contains the token `[MTC:<allocation uid>]`.
2. On every invoice webhook: GET the invoice, list `payments.transactions`. A transaction is **ours** if its id equals an `allocations.paypal_transaction_id` **or** its note contains the exact token `[MTC:<allocation uid>]`. Only transactions that match neither are new money (source `PAYPAL_INVOICE_PAGE`, method from PayPal).
3. If the invoice has any allocation in `RECORDING`, don't process yet: keep the event unprocessed; the sweep (§6b-4) retries it. Stuck RECORDING is resolved by §6b-3.
4. Never count a transaction twice (unique on transaction id for backfilled payments).

## 6b-2. Lost capture recovery
The order's `custom_id` is our payment `uid`. If PayPal captures but our `/capture` response never reaches the browser:
- `PAYMENT.CAPTURE.COMPLETED` webhook → find payment by `custom_id` → if still `CREATED`, run the same "finish capture" service (split check → allocations → record) as `/capture`.
- `/capture` itself: if our payment is already `CAPTURED`, return the existing receipt (200). If PayPal answers `ORDER_ALREADY_CAPTURED`, GET the order and run "finish capture", then return the receipt (200).
- **Approved but never captured** (buyer approved in the PayPal window, then closed the tab before `/capture`): the buyer gave consent, so we capture it. Triggers: the `CHECKOUT.ORDER.APPROVED` webhook, or the sweep finding a `CREATED` payment whose order is `APPROVED`. Both run the same split check first: fits → capture + finish capture; doesn't fit → don't capture, mark `VOIDED`, and the statement shows "A payment you started didn't go through — nothing was charged."
- Sweep (below): any payment `CREATED` for > 2 min → GET the order: `COMPLETED` → finish capture; `APPROVED` → split check then capture (above); `CREATED`/`PAYER_ACTION_REQUIRED` older than 3 h or expired/not found → mark `VOIDED`.
"Finish capture" is idempotent (unique capture id, unique allocations).

## 6b-3. Stuck recording recovery
Allocation in `RECORDING` for > 5 min, or before any retry of a record call: GET the invoice and search its payment notes for the exact token `[MTC:<allocation uid>]` (brackets included, so one id can't match another). Found → mark `RECORDED` (store transaction id if present). Not found → retry record-payment with the **same** `PayPal-Request-Id`.

## 6b-4. The sweep (all pending work, one function)
A single function `services/sync.py::sweep(scope)` processes: unprocessed webhook events, `CREATED` payments > 2 min, `RECORDING`/`FAILED` allocations. It runs (a) at the end of every webhook request (that invoice/order only), (b) in the **Render background worker** (`backend/worker.py`, from P3): a loop every **30 s** over all rows, plus the once-a-day reminder drafting (J7) at 09:00 America/New_York. Page loads never run it. The admin "sync" button calls the same function.

## 6b-5. `PayPal-Request-Id` only protects for 6 hours
PayPal keeps a `PayPal-Request-Id` for **6 hours** on create-order and capture (APIMatic ContextMatic lookup, P1 — `docs/APIMATIC.md`; extendable to 72 h only via a PayPal account manager). Assume the same or shorter for Invoicing calls (open check 10 tests only an immediate repeat). The sweep can retry later than that, so after 6 h a reused Request-Id **no longer prevents a duplicate**. Recovery therefore never relies on it: before any retry, record-payment first searches the invoice for the exact `[MTC:<allocation uid>]` note token (§6b-3), and capture first checks our payment state, the order status (GET) and the unique `paypal_capture_id` (§6b-2). Request-Id stays on every write as the first, short-window guard only.

## 6c. Out of scope v1 (explicit)
Refund/reversal handling of statement payments (shown, not automated), our own email sending, tax/shipping, multi-currency, real ERP connectors, multiple suppliers per account.

## 7. Configuration
`PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_BASE_URL` (sandbox only), `PAYPAL_WEBHOOK_ID`, `PAYPAL_BUSINESS_EMAIL`, `NEXT_PUBLIC_PAYPAL_CLIENT_ID`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_REASONING_EFFORT`, `DATABASE_URL` (Neon **pooled** connection string, used by the app), `DATABASE_URL_UNPOOLED` (direct, used only by Alembic migrations), `TEST_DATABASE_URL` (integration tests only: Neon `test` branch **direct/unpooled** URL locally, Postgres service in CI; conftest refuses if it equals `DATABASE_URL`/`DATABASE_URL_UNPOOLED`), `REQUIRE_TEST_DB` (CI sets `1`: DB tests fail instead of skipping), `PAYPAL_PAST_DATES_OK` (default true; false = send today's dates to PayPal if open check 8 fails, P1 D22), `ADMIN_TOKEN`, `DEMO_MODE=true`, `DEMO_COOKIE_SECRET`, `SANDBOX_BUYER_EMAIL` and `SANDBOX_BUYER_PASSWORD` (the sandbox **personal** account — every demo buyer uses this email so it can pay; both shown on the demo page; sandbox only), `IP_HASH_SECRET` (for `demo_rate` IP hashing), `APP_ENV` (`dev`|`prod`|`test`, prefixes PayPal-Request-Id keys), `SUPPLIER_NAME` (default "Demo Diamond Co."; one supplier per deployment), `APP_BASE_URL` (the **web** address; no fallback), `API_PUBLIC_URL` (the api's public address, for the PayPal webhook), `API_INTERNAL_HOSTPORT` (web service only, from Render `hostport`; optional override `API_INTERNAL_BASE`).

**Render setup:** `render.yaml` Blueprint defines the services (api: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`; web: `npm start` with the runtime proxy route forwarding `/api/*` to `http://$API_INTERNAL_HOSTPORT`; worker from P3: `python -m backend.worker`), plan `0.5c-512mb`, shared env group for non-secret values only; secrets declared per service with `sync: false` (not allowed in env groups). Python deps in `requirements.txt`. Alembic migrations are run by hand against Neon (dev, then prod at merge), never in the build. PayPal JS SDK loads with `buyer-country=US` in sandbox so the Venmo button can render. Register the webhook URL **on the api service directly** (`<API_PUBLIC_URL>/api/paypal/webhook`, not through the web proxy, so PayPal's signature headers and raw body arrive untouched) in the PayPal developer dashboard for the sandbox app and copy its id to `PAYPAL_WEBHOOK_ID`.

## 8. Testing
- Unit: money, split (Hypothesis property tests), importer, validators.
- Integration: FastAPI routes with PayPal client mocked (recorded sandbox responses as fixtures).
- Sandbox smoke script: full flow against real sandbox (manual, before video).
- Eval: `python -m eval.run_eval`.

## 9. Open checks (do in P1–P3)
1. ✅ (P1) PUT adjustment on a PARTIALLY_PAID invoice works (strip rule; `?send_to_recipient=false&send_to_invoicer=false`; PUT answers with a link → GET for the new due).
2. Venmo button renders in sandbox; whether a sandbox Venmo payment can complete.
3. Webhook delivery to the Render URL; signature verification.
4. LLM tool-calling latency acceptable for the ask box (< 8 s) on real statements (P4 eval). P0: 1.2–1.8 s for tool call + answer on a tiny case.
5. ✅ (P0) LLM key + model id: Meta Model API key works at `api.meta.ai/v1`; `muse-ember-1.0` available. (The old Llama API `api.llama.com` rejects Model API keys.) Deep health on Render re-confirms after deploy.
6. ✅ (P1) Record-payment returns `{payment_id}` (the transaction id) — store it in `allocations.paypal_transaction_id`; the `[MTC:<uid>]` note is also visible in `payments.transactions[].note`.
7. ✅ (P0) Tool calling and `response_format: json_schema` together in one call — works on `muse-ember-1.0` and `muse-spark-1.3`.
8. ✅ (P1) Past `invoice_date` (D-48) and past `DUE_ON_DATE_SPECIFIED` due date (D-18) are kept as sent; status UNPAID. `PAYPAL_PAST_DATES_OK` stays true (D22 fallback not needed).
9. ✅ (P1) A $0.05 recorded payment is accepted (no minimum).
10. ❌ (P1) PayPal does **not** honour `PayPal-Request-Id` on invoice create: a repeat fails as a duplicate number. Resume = search by `invoice_number` and adopt (P1 D4) — required, not optional.
11. ✅ (P1) Duplicate `invoice_number` → HTTP 422, `name` = `UNPROCESSABLE_ENTITY`, **`details[].issue` = `DUPLICATE_INVOICE_NUMBER`** — match on the issue, never the name.

Checks 1, 6, 8, 9, 10, 11 answered on 2026-11-09 by `python -m scripts.sandbox.p1_open_checks` (re-runnable; also R6: create with `Prefer: return=representation` returns the full invoice and send returns the payer link → 2 calls per invoice; R12: a reused send Request-Id did not replay a failure).
