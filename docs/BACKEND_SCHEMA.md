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
  demo_ready BOOLEAN NOT NULL DEFAULT false,     -- demo copy: true only after every invoice mirrored (P1 D9); demo queries require it
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX buyers_real_email ON buyers (email) WHERE demo_session IS NULL;   -- P1 D2

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
  mirror_error TEXT,                             -- last PayPal error while mirroring (create/send), shown to supplier
  mirror_attempts INT NOT NULL DEFAULT 0,
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
CREATE UNIQUE INDEX invoice_lines_inv_line ON invoice_lines (invoice_id, memo_line_id) WHERE memo_line_id IS NOT NULL;
CREATE UNIQUE INDEX invoice_lines_one_invoice ON invoice_lines (memo_line_id) WHERE memo_line_id IS NOT NULL;   -- a kept line is on exactly one invoice (P1 D21)

CREATE TABLE payments (
  id BIGSERIAL PRIMARY KEY,
  uid UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),   -- also sent as order custom_id
  buyer_id BIGINT NOT NULL REFERENCES buyers(id),
  source payment_source NOT NULL,
  paypal_order_id TEXT UNIQUE,
  paypal_capture_id TEXT UNIQUE,
  amount_cents BIGINT NOT NULL CHECK (amount_cents > 0),
  status TEXT NOT NULL,                           -- STATEMENT: CREATED → CAPTURED | CAPTURE_PENDING | FAILED | VOIDED ; OFF_PAYPAL: RECORDED ; PAYPAL_INVOICE_PAGE: CAPTURED ; any captured → REFUNDED (shown only)
  planned_split JSONB,                            -- STATEMENT: [{invoice_id, amount_cents}] computed at order time; re-checked at capture
  method TEXT,                                    -- for OFF_PAYPAL: CHECK | WIRE_TRANSFER | OTHER
  reference TEXT,                                 -- e.g. cheque number
  received_at TIMESTAMPTZ,
  unapplied_cents BIGINT NOT NULL DEFAULT 0 CHECK (unapplied_cents >= 0), -- captured money that fit no invoice (credit)
  capture_attempts INT NOT NULL DEFAULT 0,       -- bumped only after a decline → capture Request-Id suffix (P2 R2)
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), -- migration 0003 (P2 R5): notices + sweep age
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()  -- set on every status change
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
| POST | `/statement/{token}/order` | public | same as preview → `{paypal_order_id}`. First (P3b F4) GETs the PayPal invoices the split would touch (≤ 3 s in total, skipped on timeout): one paid on PayPal's own page meanwhile is backfilled and the order refused with **409 `PAID_ELSEWHERE`** "INV-1038 was paid on PayPal — your statement is updated."; not on PayPal yet → 409 `PAYPAL_NOT_READY` (P3 §M M9) |
| POST | `/statement/{token}/capture` | public | `{paypal_order_id}` → `Receipt` |
| POST | `/statement/{token}/ask` | public | `{question}` (1–500 chars) → `{answer, cited_records, looked_up, confidence, fallback, proposal_id?}` — always 200; a fallback answer files the day's REPLY (P4 D10, G1) |
| POST | `/statement/{token}/message` | public | `{text}` (1–2000 chars) → `{received: true, label, summary, checks, proposal_id?, proposal_ids, answer?, fallback}` — `summary`/`checks` written by code (2-decimal money); always 200 (P4 D10) |
| GET | `/supplier/overview` | admin/demo (P3 A1–A2; admin list excludes demo copies) | → `{totals: {open_cents, past_due_cents, paid_this_week_cents}, buyers: [{id, name, out, kept, returned, owed_cents, oldest_age_days, past_due_cents, credit_cents, last_payment, pending_proposals, promise}], pending: [ProposalCard], recent_payments: [{id, buyer, amount_cents, source, method, received_at, split, recorded_all}]}` — ledger only; "this week" = Monday 00:00 New York onward (P3 G10); no auth → 401, out of scope → 404 |
| GET | `/supplier/proposals?status=PENDING,FAILED` | admin/demo (P3 A5) | → `[Proposal]` |
| POST | `/supplier/proposals/{id}/decide` | admin/demo (demo cookie: own copy only, P3 A1) | `{decision: APPROVE|REJECT, mode?: APPLY|CREDIT|SPLIT, edits?}` → `Proposal`. ADJUSTMENT: no `mode` → decided under the buyer lock (due ≥ amount → APPLY, else CREDIT); explicit APPLY with due < amount → 409 `NEEDS_CREDIT`; SPLIT (P3b step 5; a PUT to due $0.00 on a part-paid invoice is proved) → deduct `min(due, amount)` through the APPLY path and credit the rest (`payload.amount_cents` = −deduct, `credit_cents` = the rest, `original` kept); nothing due → CREDIT; it all fits → APPLY. APPLY: ledger line + total → PayPal invoice PUT → DONE / FAILED (retry = approve again). CREDIT: DONE, no invoice/PayPal change, counted in the buyer's credit (so is a DONE SPLIT's `credit_cents`). `edits` (PENDING only): ADJUSTMENT `{amount_cents (positive) ≤ line price, reason?}` · REPLY `{text ≤ 1000}` · RETURN_CHECK `{resolution: CREDIT_LINE|ASK_FOR_SLIP, text?}` (CREDIT_LINE: the line must be KEPT → new PENDING ADJUSTMENT −price on its invoice, `evidence.source = return_check`, else 422 `NOT_KEPT`; ASK_FOR_SLIP: reply asking for the slip) or `{text}` alone as in P3a · PROMISE_NOTE `{promise_date}` (after today, ≤ 90 days, else 422 `BAD_EDIT`; original kept) · REMINDER `{text ≤ 500}`. REJECT (PENDING only) takes an optional `edits.text` reply. FAILED → retry only (409 `NOT_PENDING` on edit/reject). |
| GET | `/supplier/payments/{id}` | admin/demo (P3 A4; `?live=1` P3b step 5: after the scope check, GET each invoice of the split from PayPal — ≤ 5 s, one fetch per payment at a time — store PayPal's status/due/synced time and `mismatch`, never our amounts; timeout → the stored values) | → `{payment, buyer, allocations: [{invoice_no, amount_cents, record_state, paypal_transaction_id, error, paypal_status, paypal_due_cents, paypal_synced_at, mismatch, paypal_url}], timeline: [{at, actor, action, invoice_no}]}` — stored PayPal values in P3a (live fetch in P3b); timeline from `audit_log` (ref = payment uid or an allocation uid, or `detail.payment_id`) |
| POST | `/supplier/invoices/{id}/reminder` | admin/demo (scoped) | P3b B6 "Draft a reminder": the agent drafts one reminder (one `json_schema` call, validated like an answer + names the invoice + `send_after ≥ today`) → PENDING REMINDER `ProposalCard`. 422 `NOT_PAST_DUE` / `PROMISE_ACTIVE` · 409 `REMINDER_PENDING` (one per invoice) · 429 `LIMIT_REACHED` (P4 LLM caps, per buyer + global) · 503 `DRAFT_FAILED` (fallback or validation failed — nothing created). `BuyerDetail.invoices[].reminder` = `AVAILABLE` \| `PENDING` \| null |
| POST | `/supplier/import` | admin | multipart CSV (field `file`, ≤ 1 MB → else 413 `FILE_TOO_LARGE`; not UTF-8 → 422 `BAD_FILE`; demo cookie → 404) → `{created, updated, errors:[{row, msg}], mirrored, mirror_errors:[{invoice_no, msg}], setting_up, buyers:[{id, name, statement_url, paypal_setup}]}` — any row error → nothing written; new invoices go on PayPal through phase M's setup (`mirror.start_setup`), the request waits ≤ 20 s and the rest finishes in the background / by the sweep (P3b B7, F8, G7) |
| GET | `/supplier/import/sample.csv` | public | the three-buyer sample (`build_demo_csv(today, buyer@example.com)`), built on request with today's dates (P3b G6; demo data, no secrets) |
| POST | `/supplier/sync/{invoice_id}` | admin | → refreshed invoice from PayPal |
| POST | `/paypal/webhook` | paypal | PayPal event → verify (raw body spliced into `verify-webhook-signature`) → stored once (unique `paypal_event_id`) → 200 → processed in a BackgroundTask (P3b 1a/1b). No `PAYPAL_WEBHOOK_ID` → 503 `NOT_CONFIGURED`; not an event / missing transmission headers → 400; signature not verified → 400 `NOT_VERIFIED`, not stored; PayPal's verify unreachable → 503 `PAYPAL_UNAVAILABLE` (PayPal retries). Unknown invoice/order/type → stored, `error = 'ignored: unknown'` |
| GET | `/statement/{token}/payments/{id}` | public | → `Receipt` (for the receipt page; CAPTURED / CAPTURE_PENDING / RECORDED off-PayPal payments — P3 A9) |
| POST | `/statement/{token}/payments/{id}/refresh` | public | → re-runs `resolve_recording` for that payment's RECORDING/FAILED allocations (P2 R4) → `Receipt` |
| GET | `/config` | public | → `{paypal_client_id, demo_mode}` — loads the PayPal JS SDK at runtime (P2 D12) |
| GET | `/supplier/buyers/{id}` | admin/demo | → buyer, memos, invoices, messages, proposals, payments, statement link |
| POST | `/supplier/payments/record` | admin/demo | `{buyer_id, client_uid, method: CHECK|WIRE_TRANSFER|OTHER, reference, amount_cents, invoice_ids?, received_on?}` (P3 A9; `client_uid` makes it idempotent — same uid for another buyer → 409 `CLIENT_UID_REUSED`; `received_on` in the future → 422 `DATE_IN_FUTURE`; split errors as `/preview`) → `{payment_id, source: OFF_PAYPAL, status: RECORDED, method, reference, amount_cents, received_at, allocations, repeat}`; recorded on each PayPal invoice afterwards with the same method/date and `[MTC:<uid>] Cheque <reference>` |
| POST | `/statement/{token}/setup` | token | P3 §M M9: buyer not yet on PayPal → run or join the same idempotent `mirror_invoices` for that buyer, wait ≤ 10 s → `{paypal_setup: READY\|SETTING_UP\|FAILED}`; already READY → returns at once, no PayPal call |
| POST | `/supplier/buyers/{id}/setup` | admin/demo | same as above for a buyer in scope (404 otherwise) |
| GET | `/demo/whoami` | demo cookie | → `{statement_token \| null, buyers: [{id, name, statement_token}]}` — the cookie's own ready copy; `statement_token` is Northside's (landing / back link), `buyers` lists all three (P3 §M); the "See it as Demo Diamond Co." link shows only on a statement in that list (P3 A10, G13) |
| POST | `/demo/start` | public (DEMO_MODE, ≤ 15 per IP per hour, ≤ 60 per hour overall) | → creates a fresh demo buyer from the seed (invoice numbers suffixed `-<6 chars>`), mirrors to PayPal, sets demo cookie → `{statement_token, buyer_id, ms}` |

Errors: `{error: {code, message, paypal_debug_id?}}`; 422 validation, 404 unknown token, 429 `RATE_LIMITED` / `DEMO_HOURLY_LIMIT`, 503 `NOT_CONFIGURED` (lists missing setting names, never values) / `PAYPAL_UNAVAILABLE` (transient: 5xx/429/timeout after retries, or the 20 s demo deadline), 502 `PAYPAL_REJECTED` (deterministic PayPal 4xx during demo start; debug_id logged), 409 `STATEMENT_CHANGED` / `PAYMENT_FAILED` / `ORDER_NOT_APPROVED` (an already-captured payment returns its existing receipt with 200), 402 `PAYMENT_DECLINED` (SDK restarts checkout), 503 `CAPTURE_UNCONFIRMED`, 422 `AMOUNT_NOT_POSITIVE` / `AMOUNT_EXCEEDS_SELECTED` / `NOTHING_SELECTED` / `INVOICE_NOT_OPEN`, 422 `TEXT_EMPTY` / `TEXT_TOO_LONG` (ask/message), 409 `NEEDS_CREDIT` ("Invoice already paid — record it as credit instead") / `NOT_PENDING` (decide), 502 PayPal failure (state saved, retry shown). Full P2 list: `docs/phases/P2.md` D13.

Phase M (P3 §M M9): `Statement`, overview buyer rows and `BuyerDetail` carry `paypal_setup` (`READY` \| `SETTING_UP` \| `FAILED`); `/order` and `/supplier/payments/record` answer **409 `PAYPAL_NOT_READY`** while any of the buyer's invoices is not yet on PayPal.

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
  "credit_cents": 0,                               // Σ unapplied_cents of this buyer's payments + Σ DONE CREDIT proposals' payload.credit_cents (P3 A6, one helper `services/credit.py`; informational — doesn't lower owed)
  "notices": [{"kind":"PAYMENT_VOIDED","payment_id":21,"at":"D-0"}],   // kinds: PAYMENT_VOIDED | PAYMENT_CREDITED | RECORDING_PENDING
  "pending_proposals": 1,
  "requests": [{"id": 4, "kind": "ADJUSTMENT", "summary": "−$20.00 on INV-1051 for S-120", "status": "WAITING", "created_at": "D-0", "reply": null}],   // P4 D17 + P3 A8/A11: newest first; summary computed from the payload (a decided deduction reads "Approved — $20.00 off INV-1051" or "Approved as $20.00 credit on your account"); status WAITING | APPROVED | DECLINED | ANSWERED; reply = the supplier's text
  "demo_login": {"email": "<SANDBOX_BUYER_EMAIL>", "password": "<sandbox>"} }   // only in DEMO_MODE for demo buyers (P2 D2); invoices[].paypal_url = payer_view_url

// Receipt
{ "payment_id": 17, "paypal_capture_id": "4CN029778W2847743", "amount_cents": 500000,
  "allocations": [{"invoice_no":"INV-1038","amount_cents":350000,"recorded":true},
                  {"invoice_no":"INV-1042","amount_cents":150000,"recorded":true}],
  "new_owed_cents": 342000 }
// P2 D11 adds: status, received_at, credit_cents, resplit, and per allocation due_after_cents + paypal_url
```

## 4. PayPal invoice body (create)
```jsonc
{ "detail": { "currency_code": "USD", "invoice_number": "<paypal_invoice_number>", "reference": "M-201",
              "invoice_date": "<issued_on, YYYY-MM-DD>",
              "payment_term": {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": "<due_on, YYYY-MM-DD>"},
              "memo": "Kept from memo M-201 · ERP INV-1038",          // private to the merchant
              "note": "See everything you owe and pay several invoices at once: <APP_BASE_URL>/s/<token>" },   // visible to the buyer
  "invoicer": { "email_address": "<PAYPAL_BUSINESS_EMAIL>", "business_name": "<SUPPLIER_NAME>" },   // CONFIRMED by the P1 sandbox run (D7)
  "primary_recipients": [{ "billing_info": { "business_name": "Northside Jewelers", "email_address": "<buyer email>" } }],   // bill-to name, CONFIRMED (D7)
  "items": [{ "name": "S-104 Stud pair (memo M-201)", "description": "SKU S-104 · memo M-201", "quantity": "1",
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
       "sku":{"type":["string","null"]}, "skus":{"type":"array","items":{"type":"string"}}, "amount_cents":{"type":["integer","null"]},
       "date":{"type":["string","null"]}, "invoice_no":{"type":["string","null"]}, "reason":{"type":["string","null"]} }} } }
// P4: `skus` = every item a RETURN_CLAIM names (Z7); `date` = the phrase as the buyer wrote it, resolved by code. All fields are required in strict mode (nullable).

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
Buyer key = `(buyer_email, demo_session)`; real imports have `demo_session = NULL` (update the existing buyer); each demo copy creates a new buyer. Rules: a line on an INVOICE becomes KEPT; a RETURN makes it RETURNED; otherwise OUT. Prices in dollars → cents.

**File contract (P1 D20):** one header row containing every column above (unused cells empty); row numbers 1-based, header = row 1; dates ISO `YYYY-MM-DD`; price matches `^\d+(\.\d{1,2})?$`; the file is an **upsert**; counts = entities created / changed (unchanged rows count neither); validate every row first — if any error, write nothing and return all errors `{row, msg}`. Existing buyers are locked with `pg_advisory_xact_lock(buyer_id)` (invariant 9).

**References (P1 build B3):** every memo/invoice a row refers to must be declared in the same file — an ERP export is a snapshot of what it covers.

**Validation errors (each → row error):** unknown `record_type`; missing required column value; bad date or price; MEMO/LINE/RETURN/INVOICE/INVOICE_LINE referring to an unknown memo or invoice; duplicate key in the file (same memo_no, same memo_no+sku, same invoice_no, same invoice_no+sku); INVOICE_LINE whose SKU has no LINE; a line on two invoices; a SKU both on an invoice and RETURNED; an invoice with zero lines; an INVOICE_LINE whose memo isn't the invoice's memo (one invoice = one memo, P1 build B5); a RETURN for a SKU with no LINE; `issued_on > due_on`; any change (lines, dates, total) to an invoice that already has a `paypal_invoice_id` (mirrored invoices are immutable).

## 7. Eval file (`eval/messages.jsonl`)
Each eval row runs against the **§Seed** dataset as in-memory Facts (`facts_from_seed`, equal to the database's — tested) with D = the eval run date; `"statement"` is `"seed"` or `"seed_paid_5000"` (after a $5,000 payment). `"job"` is `"message"` (triage) or `"ask"` (Job A, with `expected.cited_records` + `expected.facts`). Actions use code rules: `ADJUSTMENT:INV-1051:-2000`, `RETURN_CHECK:NO_RECORD:S-103`, `ANSWER:ALREADY_CREDITED:S-102`, `REPLY:UNKNOWN:S-199`, `PROMISE_NOTE:next:FRI`, multi-item parts joined with `|`. Dev set = `eval/messages.jsonl` (`source: written`); held-out = `eval/heldout.jsonl` (`source: written-heldout`, written by the founder, unseen during development).
`{"id":"m01","statement":"seed","text":"...","expected":{"label":"RETURN_CLAIM","fields":{"sku":"S-102"},"action":"RETURN_CHECK:NO_RECORD"},"source":"written|written-heldout","job":"message"}`

## 8. Rules the endpoints follow (resolved Nov 9)
**Auth per route**
| Route group | Auth |
|---|---|
| `/statement/{token}/*` | statement token only |
| `/supplier/overview`, `/supplier/buyers/{id}`, `/supplier/payments/{id}`, `/supplier/proposals*`, `/supplier/payments/record` | admin **or** demo cookie (demo sees only its own session's buyers) |
| `/supplier/import`, `/supplier/sync/{id}` | admin only (demo visitors cannot import) |
| `/demo/start` | public, DEMO_MODE, ≤ 15 per IP-hash per hour (429 `RATE_LIMITED`) and ≤ 60 per hour overall (429 `DEMO_HOURLY_LIMIT`), hour buckets in **UTC** (`demo_rate`, `ip_hash='global'`, atomic upsert, counted **only on success**; WARNING log at 80% of global); client IP = first address in `X-Forwarded-For` (set by Render's edge, passed on by the web proxy), else the socket address — spoofable by calling the api directly, acceptable for a sandbox demo |
| `/paypal/webhook` | PayPal signature |
| `/health` public · `/health/deep` admin only | |

**Demo copies:** every `/demo/start` (incl. "Start again") creates a **new** `demo_session` and buyer; the signed cookie `mtc_demo` (7 days, HttpOnly, Secure, SameSite=Lax) is set only after a successful start; every demo query also requires `buyers.demo_ready = true` (P1 D8, D9).

**Proposal shapes** (`proposals.payload` / `proposals.evidence`)
- ADJUSTMENT: `payload = {invoice_id, amount_cents, reason, sku}` + on approval `{mode: APPLY|CREDIT, credit_cents, original: {amount_cents, reason}}` (the effective values, written under the lock before any PayPal call — P3 R1); `result` merges `{paypal_due_cents, paypal_status, attempts, error, debug_id, reply_text}` — `amount_cents` negative (reduces the invoice, same sign as `invoice_lines`); `evidence = {source: "seed"|"agent", checks: [...], records: [...], due_cents?}`. ≤ line price at triage; **≤ invoice due at approval** (else 409 `NEEDS_CREDIT`). Approval adds an `invoice_lines` row with `memo_line_id NULL` and lowers `total_cents`.
- RETURN_CHECK (only `NO_RECORD`): `payload = {sku}`. PROMISE_NOTE: `{promise_date, amount_cents?}`. REPLY: `{reason, sku?}`; a fallback REPLY: `{fallback_day, items: [{message_id, kind, text, reason}]}` (one pending per buyer per day). `evidence.internal = true` marks guard notes the buyer never sees. A repeat of a pending deduction files nothing (the buyer is told "Already sent").
- Seed (P1 D3): message `kind="MESSAGE"`, `ai_result={"seeded": true}`, audit actor `system`; one ADJUSTMENT −2000 on INV-1051. P4 replaces it with a proposal the live agent creates at demo start (D13: the agent's when triage gives exactly `ADJUSTMENT:INV-1051:-2000`, else this seed one; live: agent 4/4).
- REPLY from the D23 reconcile (P3b X6, system-made, `evidence.internal = true`): `payload = {reason: "$X paid twice on INV-… — held as credit; refund it from PayPal if they want it back."}`; the money sits in the duplicate payment's `unapplied_cents`.
- REMINDER (P3b B6): `payload = {invoice_id, text, send_after}` + on approval `{attempt: {day, n}, original?: {text}}`; `result = {sent_at, attempts, error, debug_id}`; `evidence = {source: "agent", checks, records}`. Approve (edits `{text}` ≤ 500) → under the lock count `reminders_sent` for the invoice in NY today: ≥ 2 → 409 `REMIND_LIMIT` (PayPal's limit, enforced by us — the sandbox doesn't, CLAUDE §4); else APPROVED → after commit `POST /invoices/{id}/remind` (Request-Id `<env>-rem-<invoice uid>-<YYYYMMDD>-<n>`, kept for a retry the same day) → `reminders_sent` row + DONE, or FAILED (approve again = retry). APPROVED while in flight → 409 `IN_FLIGHT`.
- REMINDER and other kinds are defined in P3/P4 specs.

**Preview / order defaults** (`/preview`, `/order`, `/supplier/payments/record`)
- neither `invoice_ids` nor `amount_cents` → all open invoices, full balance.
- `invoice_ids` only → full balance of those invoices.
- `amount_cents` only → all open invoices, oldest due first.
- both → split `amount_cents` across those invoices; amount > their balance → 422 `AMOUNT_EXCEEDS_SELECTED`.
- UI default: all open invoices checked.

**Split leftovers:** no minimum allocation. Any positive cent amount may be allocated, and an allocation can never exceed that invoice's balance. (Open check: PayPal accepts small recorded amounts; if not, reject amounts that would leave an allocation under PayPal's minimum and suggest the nearest valid amount.)

**Order → capture:** at `/order` store `planned_split`. At `/capture`, before capturing, recompute the split with current balances. Same result → capture. Different but the amount still fits the selected invoices → capture and use the new split (receipt says so). Amount no longer fits (e.g. paid meanwhile) → **first GET the order** (P2 R1): if PayPal already shows it `COMPLETED` (we captured earlier but never saw the answer), run `finish_capture` and keep whatever no longer fits as `unapplied_cents` credit (invariant 9). Only an order PayPal has **not** captured → don't call capture (PayPal has no void for an approved CAPTURE order; it simply expires), set our payment `VOIDED`, return 409 `STATEMENT_CHANGED` ("Your statement changed — please review"). **A payment with money at PayPal is never marked VOIDED.**

**PayPal-Request-Id keys:** built from `APP_ENV` (e.g. `dev`, `prod`) + the row's **`uid`** (UUID4 stored on the row, never the serial id), so keys never collide across databases, resets or environments sharing one sandbox: invoice create `<env>-inv-<invoice uid>` · send `<env>-send-<invoice uid>` · order `<env>-ord-<payment uid>` · capture `<env>-cap-<payment uid>` (after a decline `<env>-cap-<payment uid>-<n>`, P2 R2) · record payment `<env>-alloc-<allocation uid>` · adjustment PUT `<env>-adj-<proposal uid>` · remind `<env>-rem-<invoice uid>-<YYYYMMDD>-<n>`.

**PayPal invoice numbers:** in sandbox **always** `paypal_invoice_number = <erp_invoice_no>-<6 random chars>` (demo or not), so two databases seeding the same sandbox never hit `DUPLICATE_INVOICE_NUMBER`. People always see `erp_invoice_no`.

**Concurrency lock:** every money write for a buyer (split check → allocations insert → `paid_cents` update) runs in one DB transaction that first takes `SELECT pg_advisory_xact_lock(buyer_id)`. Order creation, capture finishing, off-PayPal recording, webhook backfill and approved adjustments all use it, so two writers can never split against stale balances. **Capture is the one place the lock is held across a PayPal call** (split check → capture with a 10 s timeout → allocations, one transaction); every other PayPal call happens after commit.

**Captured money that no longer fits:** if PayPal has already captured (webhook/sweep "finish capture") and the re-split can't place the full amount (balances changed meanwhile), allocate what fits oldest-first, store the rest in `payments.unapplied_cents`, add a `PAYMENT_CREDITED` notice for the buyer ("$X kept as credit on your account"), and create a REPLY proposal for the supplier ("Refund $X in PayPal or apply to the next invoice"). Never refund automatically.

**Deductions:** amount ≤ the line price **and** ≤ the invoice's current due (`total - paid`).

**Proposal edits** (`decide` with `edits`): ADJUSTMENT `{amount_cents?, reason?}` · REMINDER `{text?}` · REPLY `{text?}` · RETURN_CHECK `{resolution: CREDIT_LINE|ASK_FOR_SLIP}` · PROMISE_NOTE `{promise_date?}`. Edits are re-validated with the same rules.

**`/message` with label QUESTION:** answered by Job A; answer stored and shown; a REPLY proposal is created only if the answer fails validation.

**Reminders per day:** counted in America/New_York.

**Payment state machine (all sources)**
- STATEMENT: CREATED → CAPTURED · CREATED → CAPTURE_PENDING (PayPal capture status PENDING; no allocations until `PAYMENT.CAPTURE.COMPLETED`, P2 D6) → CAPTURED · CREATED → FAILED · CREATED → VOIDED (our status only: statement changed or order expired; no PayPal call)
- OFF_PAYPAL: RECORDED
- PAYPAL_INVOICE_PAGE: CAPTURED (created by webhook backfill)
- any CAPTURED/RECORDED → REFUNDED (shown, not automated in v1)
