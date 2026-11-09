# Memo-to-Cash — Backend Schema (DB, API, AI, import)

All money is **integer cents** (`BIGINT`), currency USD in v1. Timestamps `TIMESTAMPTZ`, dates `DATE`.

## 1. Database (Postgres)
```sql
CREATE TYPE line_status AS ENUM ('OUT','RETURNED','KEPT');
CREATE TYPE ledger_status AS ENUM ('DRAFT','OPEN','PARTIALLY_PAID','PAID','CANCELLED');  -- OUR view, derived from allocations
-- PayPal's own status is stored raw in invoices.paypal_status (TEXT): any value PayPal returns
-- (DRAFT, SENT, UNPAID, PAYMENT_PENDING, PARTIALLY_PAID, PAID, MARKED_AS_PAID, PARTIALLY_REFUNDED, REFUNDED, CANCELLED, ...)
CREATE TYPE payment_source AS ENUM ('STATEMENT','PAYPAL_INVOICE_PAGE','OFF_PAYPAL');
CREATE TYPE proposal_kind AS ENUM ('ADJUSTMENT','RETURN_CHECK','REMINDER','REPLY','PROMISE_NOTE');
CREATE TYPE proposal_status AS ENUM ('PENDING','APPROVED','REJECTED','DONE','FAILED');
CREATE TYPE actor AS ENUM ('system','supplier','buyer','agent','paypal');

CREATE TABLE buyers (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  statement_token TEXT NOT NULL UNIQUE,          -- secrets.token_urlsafe(32)
  is_demo BOOLEAN NOT NULL DEFAULT false,
  demo_session TEXT,                             -- set for per-visitor demo copies
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE memos (
  id BIGSERIAL PRIMARY KEY,
  buyer_id BIGINT NOT NULL REFERENCES buyers(id),
  memo_no TEXT NOT NULL,                         -- as shown to people, e.g. M-201
  sent_on DATE NOT NULL,
  due_back_on DATE,
  UNIQUE (buyer_id, memo_no)                     -- per buyer, so demo copies never collide
);

CREATE TABLE memo_lines (
  id BIGSERIAL PRIMARY KEY,
  memo_id BIGINT NOT NULL REFERENCES memos(id),
  sku TEXT NOT NULL,
  description TEXT NOT NULL,
  price_cents BIGINT NOT NULL CHECK (price_cents > 0),
  status line_status NOT NULL DEFAULT 'OUT',
  returned_on DATE,
  return_ref TEXT,
  UNIQUE (memo_id, sku)
);
CREATE INDEX ON memo_lines (sku);

CREATE TABLE invoices (
  id BIGSERIAL PRIMARY KEY,
  buyer_id BIGINT NOT NULL REFERENCES buyers(id),
  erp_invoice_no TEXT NOT NULL,                  -- as shown to people, e.g. INV-1038
  uid UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
  paypal_invoice_number TEXT NOT NULL UNIQUE,    -- sent to PayPal; erp_invoice_no || '-' || 6 random chars (always in sandbox)
  memo_id BIGINT NOT NULL REFERENCES memos(id),  -- one invoice = kept lines of one memo (PRD §8)
  paypal_invoice_id TEXT UNIQUE,
  payer_view_url TEXT,
  issued_on DATE NOT NULL,
  due_on DATE NOT NULL,
  total_cents BIGINT NOT NULL CHECK (total_cents >= 0),   -- Σ invoice_lines.amount_cents
  paid_cents BIGINT NOT NULL DEFAULT 0 CHECK (paid_cents >= 0), -- Σ allocations (ledger rule)
  ledger_status ledger_status NOT NULL DEFAULT 'DRAFT',
  paypal_status TEXT,                            -- raw PayPal status, mirrored on every GET/webhook
  paypal_due_cents BIGINT,                       -- PayPal's due_amount at last sync
  paypal_synced_at TIMESTAMPTZ,
  mismatch TEXT,                                 -- set when PayPal due != our due; shown to supplier
  CHECK (paid_cents <= total_cents),
  UNIQUE (buyer_id, erp_invoice_no)
);
CREATE INDEX ON invoices (buyer_id, ledger_status, due_on);

CREATE TABLE invoice_lines (
  id BIGSERIAL PRIMARY KEY,
  invoice_id BIGINT NOT NULL REFERENCES invoices(id),
  memo_line_id BIGINT REFERENCES memo_lines(id),   -- NULL for adjustment lines
  name TEXT NOT NULL,
  amount_cents BIGINT NOT NULL                    -- negative for adjustments
);

CREATE TABLE payments (
  id BIGSERIAL PRIMARY KEY,
  uid UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),   -- also sent as order custom_id
  buyer_id BIGINT NOT NULL REFERENCES buyers(id),
  source payment_source NOT NULL,
  paypal_order_id TEXT UNIQUE,
  paypal_capture_id TEXT UNIQUE,
  amount_cents BIGINT NOT NULL CHECK (amount_cents > 0),
  status TEXT NOT NULL,                           -- STATEMENT: CREATED → CAPTURED | FAILED | VOIDED ; OFF_PAYPAL: RECORDED ; PAYPAL_INVOICE_PAGE: CAPTURED ; any captured → REFUNDED (shown only)
  planned_split JSONB,                            -- STATEMENT: [{invoice_id, amount_cents}] computed at order time; re-checked at capture
  method TEXT,                                    -- for OFF_PAYPAL: CHECK | WIRE_TRANSFER | OTHER
  reference TEXT,                                 -- e.g. cheque number
  received_at TIMESTAMPTZ,
  unapplied_cents BIGINT NOT NULL DEFAULT 0 CHECK (unapplied_cents >= 0)  -- captured money that fit no invoice (credit)
);

CREATE TABLE allocations (
  id BIGSERIAL PRIMARY KEY,
  uid UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),   -- used in the note token [MTC:<uid>]
  payment_id BIGINT NOT NULL REFERENCES payments(id),
  invoice_id BIGINT NOT NULL REFERENCES invoices(id),
  amount_cents BIGINT NOT NULL CHECK (amount_cents > 0),
  record_state TEXT NOT NULL DEFAULT 'PENDING',   -- PENDING → RECORDING → RECORDED | FAILED (retryable)
  paypal_recorded_at TIMESTAMPTZ,
  paypal_transaction_id TEXT UNIQUE,              -- from record-payment response if PayPal returns one (open check)
  record_started_at TIMESTAMPTZ,                  -- set when record_state becomes RECORDING
  -- the record-payment note always carries the token '[MTC:<allocation uid>]' (with brackets) so webhooks can match even without a transaction id
  paypal_record_error TEXT,
  attempts INT NOT NULL DEFAULT 0,
  UNIQUE (payment_id, invoice_id)
);

CREATE TABLE messages (
  id BIGSERIAL PRIMARY KEY,
  buyer_id BIGINT NOT NULL REFERENCES buyers(id),
  text TEXT NOT NULL CHECK (length(text) <= 2000),
  kind TEXT NOT NULL,                             -- QUESTION_UI | MESSAGE
  ai_result JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE proposals (
  id BIGSERIAL PRIMARY KEY,
  uid UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
  buyer_id BIGINT NOT NULL REFERENCES buyers(id),
  kind proposal_kind NOT NULL,
  payload JSONB NOT NULL,                         -- e.g. {invoice_id, amount_cents, reason}
  evidence JSONB NOT NULL,                        -- e.g. {check:"NO_RECORD", records:[...]}
  source_message_id BIGINT REFERENCES messages(id),
  status proposal_status NOT NULL DEFAULT 'PENDING',
  decided_at TIMESTAMPTZ,
  result JSONB
);

CREATE TABLE reminders_sent (
  id BIGSERIAL PRIMARY KEY,
  invoice_id BIGINT NOT NULL REFERENCES invoices(id),
  sent_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE webhook_events (
  id BIGSERIAL PRIMARY KEY,
  paypal_event_id TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL,
  payload JSONB NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  processed_at TIMESTAMPTZ,
  error TEXT
);

CREATE TABLE audit_log (
  id BIGSERIAL PRIMARY KEY,
  at TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor actor NOT NULL,
  action TEXT NOT NULL,                           -- e.g. invoice.mirrored, payment.captured, allocation.recorded
  ref TEXT,
  detail JSONB
);

CREATE TABLE demo_rate (
  ip_hash TEXT NOT NULL,                          -- sha256(ip + secret), never the raw IP
  window_start TIMESTAMPTZ NOT NULL,              -- hour bucket
  count INT NOT NULL DEFAULT 0,
  PRIMARY KEY (ip_hash, window_start)
);

CREATE TABLE llm_usage (
  day DATE NOT NULL,
  scope TEXT NOT NULL,                            -- 'global' or statement token hash
  calls INT NOT NULL DEFAULT 0,
  PRIMARY KEY (day, scope)
);
```
Derived (not stored): invoice `due_cents = total_cents - paid_cents`; buyer owed = Σ due; age = today − `issued_on` of oldest unpaid.

## 2. API (FastAPI, prefix `/api`)
Auth: **public** = statement token in path; **admin** = `Authorization: Bearer ADMIN_TOKEN`; **demo** = signed demo cookie, scoped to that visitor's own demo buyer (DEMO_MODE only); **paypal** = webhook signature. Which routes accept the demo cookie and which are admin-only: see the table in §8.

| Method | Path | Auth | Body → Response |
|---|---|---|---|
| GET | `/health` | public | → `{ok, sandbox: true, db: bool, env}` (never 500) |
| GET | `/health/deep` | admin | → `{db: bool, paypal_token: bool, llm: {ok, model, ms}}` (no secrets) |
| GET | `/statement/{token}` | public | → `Statement` (§3) |
| POST | `/statement/{token}/preview` | public | `{invoice_ids?: [], amount_cents?}` → `{allocations:[{invoice_no, amount_cents}], total_cents}` / 422 |
| POST | `/statement/{token}/order` | public | same as preview → `{paypal_order_id}` |
| POST | `/statement/{token}/capture` | public | `{paypal_order_id}` → `Receipt` |
| POST | `/statement/{token}/ask` | public | `{question}` → `AnswerResult` |
| POST | `/statement/{token}/message` | public | `{text}` → `{received: true, proposal_id?}` |
| GET | `/supplier/overview` | admin | → buyers with owed/overdue/age/last payment + pending proposals count |
| GET | `/supplier/proposals?status=PENDING` | admin | → `[Proposal]` |
| POST | `/supplier/proposals/{id}/decide` | admin | `{decision: APPROVE|REJECT, edits?}` → `Proposal` |
| GET | `/supplier/payments/{id}` | admin | → payment + allocations + PayPal statuses + audit |
| POST | `/supplier/import` | admin | multipart CSV → `{created, updated, errors:[{row, msg}]}` |
| POST | `/supplier/sync/{invoice_id}` | admin | → refreshed invoice from PayPal |
| POST | `/paypal/webhook` | paypal | PayPal event → 200 always after storing; processes idempotently |
| GET | `/statement/{token}/payments/{id}` | public | → `Receipt` (for the receipt page) |
| GET | `/supplier/buyers/{id}` | admin/demo | → buyer, memos, invoices, messages, proposals, payments, statement link |
| POST | `/supplier/payments/record` | admin/demo | `{buyer_id, method: CHECK|WIRE_TRANSFER|OTHER, reference, amount_cents, invoice_ids?}` → payment + allocations |
| POST | `/demo/start` | public (DEMO_MODE, ≤ 5 per IP per hour) | → creates a fresh demo buyer from the seed (invoice numbers suffixed `-<6 chars>`), mirrors to PayPal, sets demo cookie → `{statement_token}` |

Errors: `{error: {code, message, paypal_debug_id?}}`; 422 validation, 404 unknown token, 409 only for `STATEMENT_CHANGED` (an already-captured payment returns its existing receipt with 200), 502 PayPal failure (state saved, retry shown).

## 3. Response shapes
```jsonc
// Statement (dates are real ISO dates computed from the seed's relative offsets; examples below use D±n)
{ "buyer": {"name": "Northside Jewelers"},
  "supplier": {"name": "Demo Diamond Co."},
  "owed_cents": 842000, "oldest_age_days": 48, "past_due_cents": 350000,
  "memos": [{ "memo_no": "M-201", "sent_on": "D-75",
     "lines": [{"sku":"S-102","description":"Oval 1.20ct","price_cents":120000,
                "status":"RETURNED","returned_on":"D-38","return_ref":"R-311","due_back_on":null,"invoice_no":null},
               {"sku":"S-103","description":"Pendant","price_cents":80000,
                "status":"OUT","returned_on":null,"return_ref":null,"due_back_on":"D+11","invoice_no":null}] }],
  "invoices": [{ "id": 3, "invoice_no":"INV-1042","memo_no":"M-207","total_cents":350000,"paid_cents":0,
                 "due_cents":350000,"issued_on":"D-20","due_on":"D+10","ledger_status":"OPEN",
                 "days_overdue":0,"paypal_url":"https://www.sandbox.paypal.com/invoice/p/#..." }],
  "payments": [{ "id": 17, "source":"OFF_PAYPAL","method":"CHECK","reference":"1043","amount_cents":200000,
                 "received_at":"D-2","applied":[{"invoice_no":"INV-1038","amount_cents":200000}] }],
  "credit_cents": 0,                               // Σ unapplied_cents of this buyer's payments (shown as "Credit")
  "notices": [{"kind":"PAYMENT_VOIDED","payment_id":21,"at":"D-0"}],   // kinds: PAYMENT_VOIDED | PAYMENT_CREDITED | RECORDING_PENDING
  "pending_proposals": 1 }

// Receipt
{ "payment_id": 17, "paypal_capture_id": "4CN029778W2847743", "amount_cents": 500000,
  "allocations": [{"invoice_no":"INV-1038","amount_cents":350000,"recorded":true},
                  {"invoice_no":"INV-1042","amount_cents":150000,"recorded":true}],
  "new_owed_cents": 342000 }
```

## 4. PayPal invoice body (create)
```jsonc
{ "detail": { "currency_code": "USD", "invoice_number": "<paypal_invoice_number>", "reference": "M-201",
              "invoice_date": "<issued_on, YYYY-MM-DD>",
              "payment_term": {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": "<due_on, YYYY-MM-DD>"},
              "memo": "Kept from memo M-201",
              "note": "See everything you owe and pay several invoices at once: <APP_BASE_URL>/s/<token>" },
  "invoicer": { "email_address": "<PAYPAL_BUSINESS_EMAIL>" },
  "primary_recipients": [{ "billing_info": { "email_address": "<buyer email>" } }],
  "items": [{ "name": "S-104 Stud pair (memo M-201)", "quantity": "1",
              "unit_amount": {"currency_code": "USD", "value": "1150.00"} }],
  "configuration": { "partial_payment": { "allow_partial_payment": true,
                     "minimum_amount_due": {"currency_code": "USD", "value": "1.00"} } } }
```

## 5. AI output schemas (JSON Schema, enforced via `response_format`)
```jsonc
// AnswerResult
{ "type":"object","required":["answer","cited_records","confidence","offer_escalation"],
  "properties":{ "answer":{"type":"string","maxLength":600},
    "cited_records":{"type":"array","items":{"type":"string"}},   // e.g. "memo_line:S-102", "return:R-311", "invoice:INV-1042"
    "confidence":{"enum":["low","med","high"]}, "offer_escalation":{"type":"boolean"} } }

// TriageResult
{ "type":"object","required":["label","fields"],
  "properties":{ "label":{"enum":["QUESTION","RETURN_CLAIM","DEDUCTION_REQUEST","PAYMENT_PROMISE","DISPUTE","OTHER"]},
    "fields":{"type":"object","properties":{
       "sku":{"type":["string","null"]}, "amount_cents":{"type":["integer","null"]},
       "date":{"type":["string","null"]}, "invoice_no":{"type":["string","null"]}, "reason":{"type":["string","null"]} }} } }

// ReminderDraft
{ "type":"object","required":["text","send_after"],
  "properties":{ "text":{"type":"string","maxLength":500}, "send_after":{"type":"string","format":"date"} } }
```

## 6. Import file (CSV, one file, `record_type` column)
| record_type | columns |
|---|---|
| BUYER | buyer_name, buyer_email |
| MEMO | memo_no, buyer_email, sent_on, due_back_on |
| LINE | memo_no, sku, description, price |
| RETURN | memo_no, sku, returned_on, return_ref |
| INVOICE | invoice_no, memo_no, issued_on, due_on |
| INVOICE_LINE | invoice_no, memo_no, sku |
Rules: a line on an INVOICE becomes KEPT; a RETURN makes it RETURNED; otherwise OUT. A SKU can't be both. Prices in dollars with 2 decimals → cents.

## 7. Eval file (`eval/messages.jsonl`)
Each eval row runs against a fresh copy of the **§Seed** dataset (IMPLEMENTATION_PLAN) loaded into a test database with D = the eval run date; `"statement":"seed"` names it.
`{"id":"m01","statement":"seed","text":"...","expected":{"label":"RETURN_CLAIM","fields":{"sku":"S-102"},"action":"RETURN_CHECK:NO_RECORD"},"source":"real-redacted|written"}`

## 8. Rules the endpoints follow (resolved Nov 9)
**Auth per route**
| Route group | Auth |
|---|---|
| `/statement/{token}/*` | statement token only |
| `/supplier/overview`, `/supplier/buyers/{id}`, `/supplier/payments/{id}`, `/supplier/proposals*`, `/supplier/payments/record` | admin **or** demo cookie (demo sees only its own session's buyers) |
| `/supplier/import`, `/supplier/sync/{id}` | admin only (demo visitors cannot import) |
| `/demo/start` | public, DEMO_MODE, ≤ 5 per IP-hash per hour (`demo_rate`) |
| `/paypal/webhook` | PayPal signature |
| `/health` public · `/health/deep` admin only | |

**Preview / order defaults** (`/preview`, `/order`, `/supplier/payments/record`)
- neither `invoice_ids` nor `amount_cents` → all open invoices, full balance.
- `invoice_ids` only → full balance of those invoices.
- `amount_cents` only → all open invoices, oldest due first.
- both → split `amount_cents` across those invoices; amount > their balance → 422 `AMOUNT_EXCEEDS_SELECTED`.
- UI default: all open invoices checked.

**Split leftovers:** no minimum allocation. Any positive cent amount may be allocated, and an allocation can never exceed that invoice's balance. (Open check: PayPal accepts small recorded amounts; if not, reject amounts that would leave an allocation under PayPal's minimum and suggest the nearest valid amount.)

**Order → capture:** at `/order` store `planned_split`. At `/capture`, before capturing, recompute the split with current balances. Same result → capture. Different but the amount still fits the selected invoices → capture and use the new split (receipt says so). Amount no longer fits (e.g. paid meanwhile) → don't call capture (PayPal has no void for an approved CAPTURE order; it simply expires), set our payment `VOIDED`, return 409 `STATEMENT_CHANGED` ("Your statement changed — please review").

**PayPal-Request-Id keys:** built from `APP_ENV` (e.g. `dev`, `prod`) + the row's **`uid`** (UUID4 stored on the row, never the serial id), so keys never collide across databases, resets or environments sharing one sandbox: invoice create `<env>-inv-<invoice uid>` · send `<env>-send-<invoice uid>` · order `<env>-ord-<payment uid>` · capture `<env>-cap-<payment uid>` · record payment `<env>-alloc-<allocation uid>` · adjustment PUT `<env>-adj-<proposal uid>` · remind `<env>-rem-<invoice uid>-<YYYYMMDD>-<n>`.

**PayPal invoice numbers:** in sandbox **always** `paypal_invoice_number = <erp_invoice_no>-<6 random chars>` (demo or not), so two databases seeding the same sandbox never hit `DUPLICATE_INVOICE_NUMBER`. People always see `erp_invoice_no`.

**Concurrency lock:** every money write for a buyer (split check → allocations insert → `paid_cents` update) runs in one DB transaction that first takes `SELECT pg_advisory_xact_lock(buyer_id)`. Order creation, capture finishing, off-PayPal recording, webhook backfill and approved adjustments all use it, so two writers can never split against stale balances. **Capture is the one place the lock is held across a PayPal call** (split check → capture with a 10 s timeout → allocations, one transaction); every other PayPal call happens after commit.

**Captured money that no longer fits:** if PayPal has already captured (webhook/sweep "finish capture") and the re-split can't place the full amount (balances changed meanwhile), allocate what fits oldest-first, store the rest in `payments.unapplied_cents`, add a `PAYMENT_CREDITED` notice for the buyer ("$X kept as credit on your account"), and create a REPLY proposal for the supplier ("Refund $X in PayPal or apply to the next invoice"). Never refund automatically.

**Deductions:** amount ≤ the line price **and** ≤ the invoice's current due (`total - paid`).

**Proposal edits** (`decide` with `edits`): ADJUSTMENT `{amount_cents?, reason?}` · REMINDER `{text?}` · REPLY `{text?}` · RETURN_CHECK `{resolution: CREDIT_LINE|ASK_FOR_SLIP}` · PROMISE_NOTE `{promise_date?}`. Edits are re-validated with the same rules.

**`/message` with label QUESTION:** answered by Job A; answer stored and shown; a REPLY proposal is created only if the answer fails validation.

**Reminders per day:** counted in America/New_York.

**Payment state machine (all sources)**
- STATEMENT: CREATED → CAPTURED · CREATED → FAILED · CREATED → VOIDED (our status only: statement changed or order expired; no PayPal call)
- OFF_PAYPAL: RECORDED
- PAYPAL_INVOICE_PAGE: CAPTURED (created by webhook backfill)
- any CAPTURED/RECORDED → REFUNDED (shown, not automated in v1)
