# Memo-to-Cash — Implementation Plan

Deadline: **Thu Nov 12, 12:00pm PT / 3:00pm ET**. Target: **submitted Wed Nov 11, 11:00pm ET** (12h buffer).
Way of working: each phase = spec check → code → tests → commit → deploy. Nothing moves on with red tests.

## Phase P0 — Skeleton live (Mon)
**Spec: `docs/phases/P0.md` (source of truth for tasks, setup and done-when).** Summary:
- [ ] Next.js (TS, Tailwind v4) at repo root; FastAPI at `backend/main.py`; `render.yaml` Blueprint: web + api services (Starter), web rewrites `/api/*` to api
- [ ] Neon (prod = `main` branch; local = `dev`; tests = `test`); Alembic with an empty baseline (tables in P1)
- [ ] `backend/core/config.py` (pydantic-settings, `.env`, sandbox guard; only `PAYPAL_BASE_URL`, `APP_ENV`, `DATABASE_URL` required)
- [ ] `/api/health` → `{ok, sandbox, db, env}`; `/api/health/deep` (admin) → `{db, paypal_token, llm:{ok, model, ms}}`
- [ ] Llama key + model id confirmed (`python -m scripts.llama_smoke`)
- [x] `CLAUDE.md`, `PROJECT.md`, `.claude/` already in the repo
- [ ] GitHub Actions: ruff + pytest + `tsc --noEmit` + eslint
**Done when:** every box in `docs/phases/P0.md §4` is ticked.

## Phase P1 — Data + PayPal mirror (Mon → Tue morning)
- [ ] Integration-test database: `tests/conftest.py` migrates `TEST_DATABASE_URL` to head and truncates between tests; CI adds a `postgres:16` service and sets `TEST_DATABASE_URL`
- [ ] Every PayPal call built with the APIMatic plugin → row in `docs/APIMATIC.md`, `[apimatic]` in the commit; one screenshot → `docs/img/apimatic-1.png`; **submit APIMatic claim form at end of P1** (repo link)
- [ ] SQLAlchemy models + Alembic migration for **all tables** in `BACKEND_SCHEMA.md §1`
- [ ] `services/importer.py` (CSV per BACKEND_SCHEMA §6) + `seed/demo_seed.py`: builds the **§Seed** data as an in-memory CSV with dates computed from D, then runs it **through the importer** (same code path as a real import), then mirrors to PayPal
- [ ] `paypal/client.py` (token cache, retries, `PayPal-Request-Id`, debug_id logging)
- [ ] `paypal/invoices.py`: create, send, get, record_payment, put_adjustment, remind, search
- [ ] Mirror on import; idempotent; statement link in each invoice note
- [ ] `POST /demo/start` (per-visitor copy, suffixed invoice numbers, demo cookie)
- [ ] **Open checks 1, 8, 9:** PUT on PARTIALLY_PAID invoice; past `invoice_date`/due date accepted; tiny recorded payment accepted (`scripts/sandbox/`)
**Tests:** importer (good file, re-import no-op, bad rows), `test_dates.py` (seed offsets, ages, overdue days in America/New_York), client (mocked 5xx retry, 429), mirror idempotency, Request-Id keys use env + uid.
**Done when:** `/demo/start` (via importer) creates 3 PayPal sandbox invoices with memo refs, suffixed invoice numbers and PayPal due dates equal to ours; re-running the import for the same buyer creates none.

## Phase P2 — Statement + one-payment-many-invoices (Tue)
- [ ] `core/split.py` pure function + Hypothesis property tests
- [ ] Routes: `GET /statement`, `POST /preview`, `/order`, `/capture`
- [ ] `services/payments.py`: capture → payment + allocations (one transaction) → record on each PayPal invoice → retry state
- [ ] Statement page UI (4.1) + receipt (4.2); PayPal JS SDK buttons (PayPal, Pay Later, Venmo)
- [ ] **Open check 2:** Venmo button in sandbox
**Tests:** split properties; preview defaults (BACKEND_SCHEMA §8); capture idempotency (second `/capture` on a CAPTURED payment → same receipt, 200, nothing new written); `test_concurrent_captures_serialized` (two captures for one buyer at once → second waits, never over-allocates); approved-but-closed-tab order captured by webhook/sweep; **409 STATEMENT_CHANGED** when balance changed between order and capture; `ORDER_ALREADY_CAPTURED` → finish capture; partial record failure → retry recorded once; stuck RECORDING resolved by finding `[MTC:<uid>]`.
**Done when:** sandbox: pay $5,000 across 2 invoices → PayPal invoices show correct due amounts; receipt correct.

## Phase P3 — Supplier side + webhooks (Tue evening)
- [ ] Supplier overview (4.3), buyer detail, payment detail (4.4); `ADMIN_TOKEN` + demo "view as supplier"
- [ ] Webhook route: verify signature, store, process `INVOICING.INVOICE.PAID/CANCELLED/REFUNDED`, `CHECKOUT.ORDER.APPROVED`, `PAYMENT.CAPTURE.*`
- [ ] Proposals API + decide → adjustment PUT / remind (2/day guard)
- [ ] Record off-PayPal payment (J8, same RECORDING flow) + copy statement link
- [ ] `services/sync.py::sweep` + `backend/worker.py` (loop every 30 s + daily reminder drafting) + worker service added to `render.yaml`
- [ ] Register webhook URL in PayPal dashboard → `PAYPAL_WEBHOOK_ID`; dedupe by transaction id (TRD §6b)
- [ ] **Open check 3:** webhook delivery to Render
**Tests:** webhook dedupe; **webhook skipped while an allocation is RECORDING**, processed by the next sweep; `PAYMENT.CAPTURE.COMPLETED` finishes a lost capture; paid-on-PayPal-page backfill; reminder limit (America/New_York); demo cookie can't reach other buyers; demo can't import.
**Done when:** paying one invoice on PayPal's page updates the statement; approving a deduction changes the PayPal invoice.

## Phase P4 — Agent + eval (Wed morning)
- [ ] `agent/llm.py` (OpenAI client → Llama compat, timeout, usage caps)
- [ ] Job A answer (tools + validator), Job B triage (validator + proposal), Job C reminder draft
- [ ] `eval/messages.jsonl` (30: real redacted first), `eval/baseline.py`, `eval/run_eval.py` (`python -m eval.run_eval`)
- [ ] Prompt-injection cases in eval
- [ ] **Open checks 4, 7:** Llama latency; tool calling + json_schema in one call
**Tests:** validators (fake citations rejected, deduction > price rejected), fallback on timeout.
**Done when:** eval table printed; unsafe-action rate 0; answers cite real records.

## Phase P5 — Ship (Wed afternoon → night)
- [ ] Landing judge path (Start the demo), sandbox buyer hint
- [ ] README: one section per judging criterion with evidence; limits & workarounds; run locally; tests & eval numbers; architecture diagram
- [ ] Full sandbox smoke run on the production URL
- [ ] Record video (script below), upload public YouTube
- [ ] Devpost text, tools section (Meta Llama API; **APIMatic Context Plugin for PayPal** — how it was used, with commit examples), sponsor-tool field set to **APIMatic and Render**, README section on Render (Blueprint, services, worker), README section "Built with APIMatic Context Plugin" linking `docs/APIMATIC.md`, testing instructions
- [ ] Stage One checklist all ticked → **Submit**

### Stage One checklist
- [ ] Public repo, MIT license visible in About · [ ] Working Render URL free for judges until Dec 15 (Starter services, credits checked) · [ ] Public YouTube < 3:00, no copyrighted music/logos · [ ] PayPal central + AI meaningful · [ ] Tools section · [ ] Testing instructions · [ ] No real client names anywhere · [ ] Submitted (not draft)

### Video script (≤ 3:00)
- 0:00–0:20 Pain: memo out 2 months → invoice email → buyer can't tell what they owe. QuickBooks/Square users asking for one payment for many invoices; PayPal Invoicing doesn't have it.
- 0:20–1:20 Buyer: statement (returned / kept / owed) → asks about S-102 → answer with record → pays $5,000 once.
- 1:20–2:10 Supplier: payment already split → PayPal invoice page shows new balance → approves agent's deduction proposal.
- 2:10–2:45 Proof: eval vs baseline; PayPal limit + workaround.
- 2:45–3:00 Who it's for: memo traders first (39% of jeweler inventory on memo), any business with many invoices per customer next.

## Cut list (if late, cut in this order)
1. Reminder drafting (Job C) 2. Webhook refunds/cancel handling 3. Venmo/Pay Later buttons (keep PayPal) 4. CSV upload UI (keep seed) 5. Buyer detail page.
**Never cut:** statement payment + split + record on PayPal; Job A + B with eval; live link; video.

## After the hackathon (product)
ERP adapters (QuickBooks, jewelry ERPs), statement emails, supplier onboarding with PayPal Connect, multi-currency, real buyer pilots.

## Seed (every demo copy; D = demo start date, America/New_York)
Supplier "Demo Diamond Co." · Buyer "Northside Jewelers" (email = `SANDBOX_BUYER_EMAIL`).
| Memo | Sent | Line | Description | Price | Status |
|---|---|---|---|---|---|
| M-201 | D-75 | S-101 | Round 1.01ct | $2,350 | KEPT → INV-1038 |
| M-201 | | S-102 | Oval 1.20ct | $1,200 | RETURNED D-38, R-311 |
| M-201 | | S-103 | Pendant | $800 | OUT, due back D+11 |
| M-201 | | S-104 | Stud pair | $1,150 | KEPT → INV-1038 |
| M-207 | D-45 | S-110 | Cushion 0.90ct | $1,900 | KEPT → INV-1042 |
| M-207 | | S-111 | Tennis bracelet | $1,600 | KEPT → INV-1042 |
| M-207 | | S-112 | Emerald 1.50ct | $2,100 | RETURNED D-21, R-318 |
| M-207 | | S-113 | Halo ring | $950 | RETURNED D-21, R-318 |
| M-212 | D-20 | S-120 | Solitaire ring | $1,420 | KEPT → INV-1051 |
| M-212 | | S-121 | Hoops | $640 | RETURNED D-6, R-325 |
| M-212 | | S-122 | Pear 0.70ct | $1,300 | RETURNED D-6, R-325 |
| M-212 | | S-123 | Bangle | $780 | OUT, due back D+25 |

| Invoice | Memo | Lines | Total | Issued | Due |
|---|---|---|---|---|---|
| INV-1038 | M-201 | S-101, S-104 | $3,500 | D-48 | D-18 (overdue) |
| INV-1042 | M-207 | S-110, S-111 | $3,500 | D-20 | D+10 |
| INV-1051 | M-212 | S-120 | $1,420 | D-5 | D+25 |
Totals: 12 lines = 5 kept, 5 returned, 2 out · owed **$8,420** · oldest unpaid 48 days.
Seeded message + pending proposal: "There's a small scratch on the S-120 solitaire, please take $20 off." → ADJUSTMENT −$20 on **INV-1051** (evidence: S-120 kept on INV-1051, price $1,420, due $1,420). Chosen so it stays valid after the judge pays $5,000 (which clears INV-1038 and part of INV-1042; INV-1051 stays unpaid, so no edit of a paid or part-paid invoice is needed).
Demo copies: `paypal_invoice_number` = `INV-1038-<6 random chars>`; memo/invoice numbers shown to people stay `M-201` / `INV-1038`.
