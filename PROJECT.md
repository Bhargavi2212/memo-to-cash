# PROJECT.md — Memo-to-Cash (the whole thing on one page)

**Start here.** This is the map. Details live in `docs/`. Claude Code reads this first, then `CLAUDE.md`.

## What
Buyers who take goods **on memo** (consignment) get **one statement link** that shows what they kept, what they returned (credited) and what they owe — and they **pay many PayPal invoices in one payment**. An AI agent (Meta Model API, Muse) explains the bill from the records and turns buyer messages into proposals the supplier approves.

## Why
- Memo is normal trade: 39% of US jewelers' store merchandise is memo inventory; goods sit 1–3 months.
- The pain is after the invoice: the buyer has to work out which memo, what was credited, what's owed across invoices, then writes a cheque; the accountant chases and matches by hand.
- Small businesses wait: >half have invoices unpaid past 30 days (avg $17.7K); payments ~9 days late.
- QuickBooks, Square and Xero/Stripe users keep asking to pay many invoices at once. Big companies get it from Versapay/Billtrust (enterprise contracts). **PayPal Invoicing doesn't have it.** We build it on PayPal.

## For whom
- **Supplier** (customer): small/mid wholesaler with an ERP and a PayPal business account. Diamonds/jewelry first.
- **Buyer**: retailer on memo; phone and desktop.
- **Judges**: PayPal DevRel/engineering/PM — review the Devpost page, video and live link only. Everything must explain itself.

## How it works (short)
ERP export → mirror invoices as **real PayPal invoices** → buyer **statement link** → **one PayPal checkout** (PayPal, Pay Later, Venmo, bank) → **split** across invoices → **recorded on each PayPal invoice** → webhooks keep it in sync → **agent** answers questions + turns messages into **proposals** → supplier approves → PayPal invoice updated / PayPal reminder sent.

## Stack
Next.js + Python FastAPI + background worker on **Render** (sponsor) · Neon Postgres · APIMatic Context Plugin for PayPal (build tooling, sponsor) · PayPal Invoicing v2, Orders v2, JS SDK, Webhooks (sandbox) · Meta Model API (`muse-ember-1.0`, JSON-schema output, tool calling).

## File structure
✅ = exists now · everything else is created in the phase shown. Update this tree when you add/move files (same commit).
```
memo-to-cash/
├── PROJECT.md                      ✅ this file — start here
├── CLAUDE.md                       ✅ build rules for Claude Code
├── README.md                       ✅ (rewritten in P5: judge path, criteria → evidence)
├── LICENSE                         ✅ MIT
├── .gitignore                      ✅
├── .gitattributes                  P0 (LF line endings)
├── .env                            ✅ local only, never committed
├── .claude/
│   ├── settings.json               ✅ blocks reading .env, force-push
│   └── commands/                   ✅ phase.md · verify.md · sandbox-smoke.md · review-money.md
├── docs/                           ✅ PRD · TRD · UI_UX · APP_FLOW · BACKEND_SCHEMA · IMPLEMENTATION_PLAN · RESEARCH · APIMATIC (sponsor proof log) · _archive/ · img/ (screenshots, P1+)
│   └── phases/                     ✅ P0.md · P0-plan.md (build plan + gaps) · P1.md (spec v2, D1–D25) · P1-plan.md · P2.md (spec v2, D1–D24 + R1–R16) — one spec per phase, written before it starts
├── scripts/                        __init__.py (P0)
│   ├── sandbox/                    ✅ statement_test.ps1 · statement_test_v2.ps1 (manual PayPal experiments) · __init__.py + p1_open_checks.py (P1, `python -m scripts.sandbox.p1_open_checks`) · p2_smoke.py (P2, order → approve in browser → capture → record)
│   ├── llm_smoke.py                P0 LLM key + model check (prints ok/model/ms only)
│   └── sandbox_smoke.py            P5 end-to-end run against the real sandbox
│
├── package.json · next.config.ts · tsconfig.json · eslint.config.mjs · AGENTS.md   P0 (Next 16.4; Tailwind v4 via Turbopack CSS loader, tokens in app/globals.css; next-env.d.ts generated, git-ignored)
├── render.yaml                     P0 Render Blueprint (web + api, `0.5c-512mb`) · P3 adds the worker
├── requirements.txt · requirements-dev.txt · pyproject.toml (ruff, pytest)  P0
├── alembic.ini                     P0 (migrations use DATABASE_URL_UNPOOLED)
├── .github/workflows/ci.yml        P0 ruff · pytest · tsc · eslint
│
├── app/                            Next.js pages
│   ├── page.tsx                    P0 shell → P2 "Start the demo"/"Start again" wired → P5 polish
│   ├── api/[...path]/route.ts      P0 runtime proxy: /api/* → api service (private network)
│   ├── layout.tsx · globals.css    P0
│   ├── s/[token]/layout.tsx · page.tsx   P2 buyer statement (noindex; client part in <Suspense>)
│   ├── s/[token]/receipt/[paymentId]/page.tsx   P2
│   └── supplier/
│       ├── page.tsx                P3 overview
│       ├── buyers/[id]/page.tsx    P3 buyer detail (copy link, record payment)
│       ├── payments/[id]/page.tsx  P3 payment detail
│       └── import/page.tsx         P3 ERP upload (admin only)
├── components/                     P2 Statement · PayPanel · MemoGroup · InvoiceList · StatusChip · Notices · DemoLogin · Receipt · StartDemo — P3–P4 AskBox · ProposalCard · RecordPaymentModal · supplier/SampleRows.tsx
├── lib/
│   ├── api.ts                      P2 typed API client
│   ├── money.ts                    P2 the only money formatter
│   ├── dates.ts                    P2 New York "today" + relative days for buyer screens
│   ├── paypal.ts                   P2 tiny PayPal JS SDK loader (URL from /api/config, no extra dependency)
│   └── money.test.ts               P2 `npm test` (node --test, Node 22 in CI)
│
├── backend/
│   ├── main.py                     P0 FastAPI app (uvicorn) — Render api service
│   ├── worker.py                   P3 background worker: sweep every 30 s + daily reminder drafting
│   ├── core/       config.py (sandbox guard) P0 · money.py P1 · dates.py P1 · split.py P2 · logging.py P2 (statement tokens out of access logs)
│   ├── db/         session.py P0 (+ get_session_factory P2) · models.py P1 · migrations/ (Alembic) P0–P2
│   ├── paypal/     client.py P1 · invoices.py P1 · orders.py P2 · webhooks.py P3
│   ├── services/   importer.py P1 · mirror.py P1 · demo.py P1 · statement.py P2 · payments.py P2 · agent.py (ask + message) P4 · sync.py (sweep) P3 · proposals.py P4 (approve → PayPal) · audit.py P1
│   ├── routes/     health.py P0 · demo.py P1 · statement.py P2 · config.py P2 (`GET /api/config`) · supplier.py P4 (decide) + P3a · paypal_webhook.py P3
│   └── agent/      llm.py P0 (smoke call) → P4 · facts.py · tools.py (Facts → prompt text) · actions.py (decide) · dates.py · prompts/triage.md (P4 step 0) · schemas.py · validate.py · caps.py · answer.py · triage.py · prompts/answer.md   P4
├── seed/
│   ├── __init__.py · demo_seed.py  P1 `python -m seed.demo_seed`: one demo copy on dev with real sandbox invoices (same path as /api/demo/start)
│   └── sample_export.csv           P3 example ERP export for the import screen (generated by `build_csv(today)`)
├── eval/                           __init__.py · results/<date>(-spike).md (P4)
│   ├── messages.jsonl              P4 30 labelled buyer messages (real redacted first)
│   ├── baseline.py                 P4 keyword rules
│   └── run_eval.py                 P4 `python -m eval.run_eval`
└── tests/
    ├── conftest.py · dbguard.py   test DB wiring (P1 D10–D12: `db` fixture, guard, schema reset)
    ├── paypal_fake.py             in-memory PayPal Invoicing (MockTransport) for mirror/demo tests
    ├── llm_fake.py                scripted fake OpenAI client for agent tests (P4)
    ├── unit/         test_config.py · test_llm.py · test_session.py · test_dbguard.py · test_money.py · test_dates.py · test_paypal_client.py · test_invoices.py · test_orders.py · test_split.py (Hypothesis) · test_validators.py
    ├── integration/  test_health.py · test_db_smoke.py · test_schema.py · test_importer.py · test_mirror.py · test_import_api.py · test_statement_api.py · test_payments_flow.py · test_webhooks.py · test_demo.py · test_scoping.py
    └── fixtures/paypal/*.json      recorded sandbox responses (no network in tests)
```

## Docs
| Doc | Answers |
|---|---|
| `docs/PRD.md` | Problem, evidence, users, scope, out of scope, success measures, assumptions |
| `docs/TRD.md` | Requirements FR-1…15 with acceptance, non-functional rules, PayPal spec, AI spec, config, open checks (repo layout → this file) |
| `docs/UI_UX.md` | Routes, design tokens, every screen and state, copy rules, video moments |
| `docs/APP_FLOW.md` | Journeys J1–J9, sequence diagrams, state machines |
| `docs/BACKEND_SCHEMA.md` | Tables, endpoints, JSON shapes, PayPal invoice body, AI schemas, CSV format, eval format |
| `docs/IMPLEMENTATION_PLAN.md` | Phases P0–P5, tasks, done-when tests, cut list, Stage One checklist, video script |
| `docs/RESEARCH.md` | Sources and our sandbox test results |
| `CLAUDE.md` | How to build: commands, definition of done, invariants, PayPal facts, commit rules |

## Status (update at the end of every task)
| Phase | State | Notes |
|---|---|---|
| Research + docs | ✅ done | Nov 8–9 |
| P0 Skeleton live | ✅ done | Nov 9: web + api live on Render (`0.5c-512mb`, virginia), Neon prod migrated to 0001, CI green, deep health all true (`muse-ember-1.0`, 1.6 s) |
| P1 Data + PayPal mirror | ✅ done (APIMatic claim deferred to P2) | Nov 9: all tables (0002 on dev/test/prod), CSV importer, PayPal client + invoices + mirror, `/api/demo/start` live — **2.05 s / 1.45 s on Render**; open checks 1/6/8/9/11 ✅, 10 ❌ (search & adopt); 205 tests, CI with Postgres. Sandbox invoice screenshot `docs/img/paypal-invoice-1.png` (statement token blacked out). Open: APIMatic screenshot + claim form (after P2, Bhargavi) |
| P2 Statement + one payment for many invoices | ✅ live (fixes PR + APIMatic claim open) | Nov 10: statement, split preview, one PayPal checkout for many invoices, capture under the buyer lock, recording on each PayPal invoice, receipt, landing "Start the demo". Live run (Bhargavi): $5,000 then $3,420, no duplicate, all recorded, owed $0. 325 Python + 5 TS tests; CI with Postgres + `next build` |
| P3 Supplier side + webhooks | ⬜ | |
| P4 Agent + eval | 🟡 | Steps 0–5b, 8, 9 done and live on Render (PR #3, #4); steps 6/7 (full eval + freeze + held-out) and 10 (docs) next |
| P5 Ship + submit | ⬜ | Target Wed Nov 11, 11pm ET |

Open checks (TRD §9): ~~1~~ ✅ P1 · ~~2 Venmo button renders~~ ✅ P2 (sandbox Venmo payment not tried) · 3 webhook to Render · 4 LLM latency on real statements · ~~5~~ ✅ P0 · ~~6~~ ✅ P1 · ~~7~~ ✅ P0 · ~~8~~ ✅ P1 · ~~9~~ ✅ P1 · ~~10~~ ❌ not honoured (search & adopt) · ~~11~~ ✅ · ~~12 capture shape + NO_SHIPPING~~ ✅ P2 · **13 items in PayPal's window — still open** (not visible in the popup's main view).

## Decisions log (newest first — add one line per decision)
- 2026-11-11 **P4 step 9 live on Render (open check 4 closed):** "Start the demo" 3,362 / 2,211 / 2,219 ms (+ 2,440 ms for the dates check) — all < 6 s; demo-start proposal source **agent 4/4, seed 0/4** (n = 4, production rows, triage 2,633 / 1,492 / 1,473 / 991 ms, no fallback); 10 asks p50 1.37 s / p95 1.97 s, 10 messages p50 1.23 s / p95 3.04 s, 0 fallbacks, labels as expected — well under the 8 s target. Step 8 fixes confirmed live (ask link under the total, "Oct 3, 2026" dates, invoice highlight 4 s). **Render log check (no buyer text, statement paths redacted): pending (Bhargavi).** Runs: `scripts/sandbox/p4_live_check.py`.
- 2026-11-10 **P4 eval decisions (Bhargavi, Z1–Z6):** three-part scoring (model correct / system correct / non-REPLY subset, fallbacks never a model pass); `max_tokens` 1600; ember only, 3 passes, **$10 eval cap**, spark once on held-out only if ember fails; approve with due < deduction → 409 `NEEDS_CREDIT` (P3a builds credit); held-out set labelled by Bhargavi, run once after the freeze commit; the claim is 0 unsafe + injections handled + guard catches + grounded answers, not "beats rules".
- 2026-11-10 **P4 step 0 eval spike** (dev set, 22 triage rows, `muse-ember-1.0`, 1 pass): **re-scored (Z1): a tie** — model correct 20/22 vs baseline 20/22; system 22/22 vs 21/22; non-REPLY 9/9 vs 8/9; **unsafe 0 for both**; first print said 22/22 vs 21/22 actions; latency p50 1.2 s / p95 2.7 s per call; 1 length cut-off. X1 gate passed by one row (w02). Dev numbers only — the held-out real rows decide the claim (`eval/results/2026-11-10-spike.md`).
- 2026-11-10 **P2 live run** (Bhargavi, live site): $5,000 then $3,420 through one PayPal checkout each, no duplicate on double click, every allocation recorded, owed $0. Open check 2 ✅ (Venmo, Pay Later, Debit or Credit Card buttons render). Pay Monthly + PayPal Credit offered at $3,420 (and $5,000). Check 13 still open: our items are on the order but not in the popup's main view. **D20 reproduced live:** paying everything makes the seeded −$20 on INV-1051 inapplicable → P3 must turn it into a $20 credit. Two bugs found and fixed in `p2-fixes`: receipt dates used the UTC day (now America/New_York, invariant 8) and receipt balances used today's due instead of the due right after that payment.
- 2026-11-10 **P2 smoke on the real sandbox** (`scripts/sandbox/p2_smoke.py`): $5,000 order with items → approved by the sandbox buyer → captured `COMPLETED` (capture id, amount, custom_id ✓) → recorded $3,500 + $1,500: **INV-1038 MARKED_AS_PAID $0 due, INV-1042 PARTIALLY_PAID $2,000 due — equal to our ledger.** Check 12 ✅. Check 13: items are on the order but **PayPal's new checkout page doesn't show them** (only the total) → docs/video no longer claim the split shows inside PayPal; Pay Later at $5,000 = Pay Monthly + PayPal Credit. R2: a sandbox-mocked decline is **not** replayed for the same capture Request-Id (suffix kept as extra safety). `payment_source.paypal` orders start `PAYER_ACTION_REQUIRED` with only a `payer-action` link; a bare checkout link without `return_url` spins after Pay (approval still succeeds) — irrelevant for the JS SDK popup.
- 2026-11-10 **P2 review fixes** (no feature cut — Bhargavi: nothing is cut in P2): BACKEND_SCHEMA §8 order→capture now says GET the order before VOIDED (R1); D5(e) receipt before recording; step 5 split into 5a–5d; smoke script calls the Orders/Invoicing clients directly; own PayPal SDK loader (no `@paypal/react-paypal-js`); spare sandbox buyer with $99,999 if the balance can't be raised.
- 2026-11-10 **Neon fixed (hotfix `7d63adb`):** Render health check → `/api/health/live` (no DB). Production `main` had been awake 100% (7.8 h of 7.8 h, ~6 CU-h/day → free 100 CU-h gone ~Nov 25–27). After the change it went idle at 11:05 UTC; cold start 0.98 s, warm 0.18 s. **Render does not apply `render.yaml` changes by itself** (health check path and earlier the deploy trigger were set by hand) → every `render.yaml` change needs a Blueprint Manual Sync or a dashboard edit.
- 2026-11-10 **Neon free-plan risk (P2 D24):** Render's health check hits `/api/health`, which queries the database every few seconds, so production Neon never scales to zero → 100 CU-h/month could run out during judging and suspend the live demo. Fix: health check path `/api/health/live` (no DB), production max 0.25 CU, adaptive worker polling in P3. Also D23: a payment made directly on PayPal's invoice page before P3 → our record fails safely (FAILED + mismatch), reconciled by P3 backfill.
- 2026-11-10 **P2 spec v2** after an independent review (R1–R16 in P2.md): never VOID a payment PayPal captured (GET order first); capture Request-Id gets an attempt suffix after a decline; payment_source mapping (paylater → paypal, card → application_context); P2 has its own retry (`/payments/{id}/refresh`, re-POST `/capture`); migration 0003 adds `payments.created_at/updated_at/capture_attempts`; `next build` in CI; token fetched before the capture lock + 10 s total cap; receipt returns before recording (BackgroundTask + polling); demo prefills $5,000; spare sandbox buyer + guest-card fallback; Pay Monthly (not Pay in 4) at $5,000; ~10 h realistic.
- 2026-11-10 **P2 spec** (`docs/phases/P2.md` D1–D22): statement from our ledger only (no PayPal on page load); order = payment CREATED + planned_split, then PayPal order with per-invoice `items`, `NO_SHIPPING`, `custom_id`/`invoice_id` from payment uid; capture under the buyer lock (10 s, no retries), issue-based errors (ORDER_ALREADY_CAPTURED → finish, INSTRUMENT_DECLINED → 402 + SDK restart), capture PENDING → `CAPTURE_PENDING`; record allocations concurrently after commit + GET for PayPal due; `resolve_recording` built now; `/api/config` serves the PayPal client id at runtime (no NEXT_PUBLIC); token redacted from access logs + browser-default referrer policy (R12 dropped no-referrer); landing start wired in P2; smoke script before the UI; demo tip $5,000 + P3 must turn a deduction on a paid INV-1051 into a credit; sandbox buyer balance raised; APIMatic claim at end of P2.
- 2026-11-09 Render `autoDeployTrigger: checksPass` never fired after green CI → switched to `commit` (P0 B4 fallback). The guard is now ours: merge to `main` only after CI is green.
- 2026-11-09 **P1 open checks (real sandbox):** 1 PUT on part-paid ✅ · 6 record returns `payment_id` ✅ · 8 past dates kept ✅ (no D22 fallback) · 9 $0.05 ✅ · **10 Request-Id NOT honoured on create** → D4 search & adopt is required · 11 duplicate = 422 `UNPROCESSABLE_ENTITY` + `details[].issue=DUPLICATE_INVOICE_NUMBER` · R6 create returns full invoice + send returns payer link → 2 calls per invoice (GET dropped) · R12 reused send key didn't replay a failure → one send key, no attempt suffix. Field paths: `invoicer.business_name`, `billing_info.business_name`.
- 2026-11-09 **P1 spec v2** after an independent review (supersedes the D1–D19 lines below; v1 in `docs/_archive/P1-draft1.md`): D1–D25 in `docs/phases/P1.md §2`. Key changes: `buyers.demo_ready` + cookie only on success; every start = new session; search by number only on retry/duplicate; store id on create, send only if DRAFT; 8 s per call / 20 s demo deadline; 503 `PAYPAL_UNAVAILABLE` vs 502 `PAYPAL_REJECTED`; limits 15/h per IP + 60/h global (UTC, count on success); test DB = Neon `test` direct URL, guarded, schema reset, `db` marker + `REQUIRE_TEST_DB`; CSV contract + validation list; `invoice_lines` uniques; `PAYPAL_PAST_DATES_OK` fallback; open checks 10–11 added; import endpoint, `put_adjustment`, `remind`, `sample_export.csv` moved to P3.
- 2026-11-09 P1 spec recheck D10–D19: test DB wiring for the real conftest/session code; CI Postgres = `ci` creds; async PayPal + sync DB (writes after gather); add `itsdangerous`, `python-multipart`; `SUPPLIER_NAME` via render.yaml + NOT_CONFIGURED checks; failed mirror → 503 and no token; global 200 demos/day; seeded proposal marked `seed` and replaced by the live agent in P4; import writes nothing on any bad row; API shapes/error codes aligned.
- 2026-11-09 P1 spec (`docs/phases/P1.md`) D1–D9: single supplier via `SUPPLIER_NAME`; importer buyer key = (email, demo_session); seed adds message+proposal after import; resumable mirror (search by number before create); `mirror_error`/`mirror_attempts` columns; 3 invoices mirrored concurrently (demo ≤ 6 s target, worker pool in P3 if slower); invoice `note` = buyer-visible link, `memo` = private ERP ref, items carry SKU/memo; demo cookie `mtc_demo` signed, 7 days; open checks 1/6/8/9 in one script.
- 2026-11-09 P0 live on Render. Blueprint needed plan id `0.5c-512mb` (Render renamed `starter`); `autoDeployTrigger: checksPass` accepted; service URLs have no suffix. Cost: $7/mo per service, no Render spend cap exists → budget section pending (limit + Muse price from Bhargavi).
- 2026-11-09 **LLM: Meta Model API instead of the Llama API.** New Meta developer keys (`LLM_…`) only work at `https://api.meta.ai/v1` (Muse models); `api.llama.com` returns 401. P0 test of all 7 chat models (json_schema, reasoning low): `muse-ember-1.0` ~1.1 s and correct, tool call + json_schema 3/3 in 1.2–1.8 s → default; `muse-spark-1.3` (3.6–5.4 s, correct) = backup; P4 eval decides. Never a `-contributor` model. `LLM_MODEL` + `LLM_REASONING_EFFORT` are settings; cut-off reply (`finish_reason=length`) or invalid JSON = fallback. Closes open checks 5 and 7.
- 2026-11-09 Neon project `memo-to-cash` (`delicate-moon-76690980`, aws-us-east-1, Postgres 17), branches `main` (prod) / `dev` / `test`, created with the Neon CLI; `dev` migrated to 0001.
- 2026-11-09 P0 plan review (`docs/phases/P0-plan.md` G1–G16): Neon AWS US East + Render `virginia`; `uvicorn` in `requirements.txt`; settings `extra="ignore"`; psycopg `prepare_threshold=None` behind Neon's pooler; OpenAPI under `/api`; proxy passes `X-Forwarded-For` unchanged and returns 502 `API_UNREACHABLE`; never set `NODE_ENV` on Render; Bhargavi opens PRs and runs production migrations.
- 2026-11-09 Render details: `APP_ENV` = dev|prod|test; `APP_BASE_URL` explicit (web address, no fallback); `/api/*` forwarded by a runtime Next.js proxy route (no build-time env needed); PayPal webhook registered on the api's public URL; secrets per service (`sync: false` not allowed in env groups); Blueprint created after the P0 merge; rate limit uses first `X-Forwarded-For` IP.
- 2026-11-09 **Switched hosting to Render** (sponsor): Blueprint `render.yaml`, Starter plan (no sleep), web (Next.js) + api (FastAPI) in P0, background worker in P3 (sweep every 30 s + daily reminders) — replaces page-load sweep and the cron endpoint. DB stays on Neon (Render free Postgres expires after 30 days). Sponsor prizes in play: APIMatic and Render (only one can be won).
- 2026-11-09 Capture holds the buyer lock across the PayPal capture call (only exception to "PayPal after commit"). Integration tests use real Postgres (Neon `test` branch locally, Postgres service in CI). Docs pushed before Vercel import.
- 2026-11-09 Sponsor prize: target **APIMatic** (Context Plugin for PayPal used in Claude Code to build the PayPal integration; low cost, fits how we build). **Not** AG Grid for now (needs AG Studio + Studio Agent Framework rebuild of supplier side, ~1 day; Supplora already strong there) — revisit only if P4 ends early. Render/Bryntum/Channel3 don't fit; others have no prize. Only one sponsor prize can be won per project.
- 2026-11-09 Review 3: repeated `/capture` on a captured payment returns the existing receipt (200); 409 only for STATEMENT_CHANGED; approved-but-uncaptured orders are captured (CHECKOUT.ORDER.APPROVED webhook or sweep) after the split check; page-load sweep is DB-only + max 1 PayPal call and never delays the page; `custom_id` = payment uid everywhere; J6 follows TRD §6b.
- 2026-11-09 Review 2: seeded complaint moved to S-120 / INV-1051 (stays valid after the judge pays); PayPal-Request-Id = env + row UUID; sandbox invoice numbers always suffixed; lost-capture recovery via `custom_id` + webhook; stuck RECORDING resolved by exact `[MTC:<uid>]` note match; no PayPal "void" (order just expires); statement API returns invoice ids, payments list, relative dates; one `sweep` for pending work (webhook end, page loads, daily cron); sample supplier rows are a frontend constant; "Past due" instead of ">30d"; seed goes through the importer.
- 2026-11-09 Review fixes: memo/invoice numbers unique per buyer + separate `paypal_invoice_number` (demo suffix); our `ledger_status` + PayPal's raw `paypal_status` as two columns; send `invoice_date` + DUE_ON_DATE_SPECIFIED so due dates match; seed dates relative to demo start; every demo copy has one seeded proposal; race-safe recording (RECORDING state + `[MTC:<allocation uid>]` note token); planned split re-checked at capture (409 if statement changed); no minimum allocation; deductions ≤ line price and ≤ invoice due; AI answer check allows computed totals; sandbox buyer login in config; manual PayPal scripts moved to `scripts/sandbox/`.
- 2026-11-09 Build the missing capability (one payment for many PayPal invoices via Orders + record per invoice), not "one statement invoice". Accept `MARKED_AS_PAID` and document it.
- 2026-11-09 Our ledger decides balances; PayPal status mirrored, mismatches flagged.
- 2026-11-09 Each demo visitor gets their own copy (no shared reset).
- 2026-11-09 (superseded) Hosting Vercel; Next.js + Python functions; Neon; Meta Llama.
- 2026-11-09 Pay Later shown as offered, not pitched as B2B financing (consumer credit).
- 2026-11-09 No WhatsApp/screenshot/voice intake (adds steps for users).
- 2026-11-08 Lane: memo/consignment B2B — empty in this hackathon (404 repos checked).

## Links
Repo: https://github.com/Bhargavi2212/memo-to-cash · Live: https://memo-to-cash-web.onrender.com (api: https://memo-to-cash-api.onrender.com) · Video: _(P5)_ · Devpost: _(P5)_
