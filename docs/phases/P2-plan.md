# Phase P2 — Build plan (how we'll execute `P2.md`)

Spec: `docs/phases/P2.md` v2 + review fixes (`d40c86c`) — source of truth (D1–D24, R1–R16). This file is the working plan: order, dependencies, checks, gaps.
Branch: `p2-statement` from `main` @ `d40c86c`. FRs: FR-3, FR-4, FR-5, FR-6, FR-12 (landing). Open checks: 2, 12, 13.
**Nothing is cut** (Bhargavi, Nov 10): every item in P2.md ships; an overrun moves P3's start.
State at start: P1 live (demo start 1.5–2 s on Render); step 0 (Neon health check) already shipped as `7d63adb` and verified (production idles 5 min after use, cold start 0.98 s). Render does **not** apply `render.yaml` changes by itself → any `render.yaml` change needs a Blueprint Manual Sync or a dashboard edit.

## 1. Who does what, and what blocks what
| Bhargavi | Needed by |
|---|---|
| Sandbox Personal buyer balance → $99,999, or a spare buyer created with $99,999 (D21); `SANDBOX_BUYER_PASSWORD` set on Render `memo-to-cash-api` | step 5 (smoke), step 13 |
| ~20 min at the browser for the step 5 smoke — **I call you when step 4 is done** — to approve the smoke-script order(s) as the sandbox buyer (D22): one PayPal-login approval, one guest-card approval | step 5 |
| Production `alembic upgrade head` (0003) **before** merge, then say **"merge"** | step 13 |
| Live run: pay $5,000 on the live site, screenshots (`statement-1`, `paypal-popup-split`, `receipt-1`, `paypal-invoice-after`), check Render logs for tokens | step 13 |
| APIMatic screenshot `docs/img/apimatic-1.png` (still open from P1) + claim form at the end of P2 (D19) | step 14 |
| Nothing on Render for G2: `next build` stays on Node 20 (same as Render); only the `.ts` tests use Node 22 in CI | — |

| Claude Code | Notes |
|---|---|
| Steps 1–12 and 14; migrations on `dev`/`test`; local sandbox runs on `dev`; step 13: open the PR and stop | never production, never merge before Bhargavi's "merge" |
| APIMatic lookups before writing `orders.py` (D19) | plugin is live in this session |

Blocks: steps 1–4 start immediately. Step 5 (smoke) needs Bhargavi at the browser — I call her after step 4. UI steps 9–12 need step 8 (APIs). Step 13 (live run) needs her prod migration 0003 and her "merge".

## 2. Steps (one commit each; tests first for money/split/idempotency; before every commit `ruff check . && ruff format --check .`, `pytest -q`, and for TS `npx tsc --noEmit && npm run lint`)
Order changed vs spec — **5a (migration) moves before step 2**, because the statement's notices need `payments.updated_at` (G1). Spec numbers in brackets.
**Every step:** tests first · `/review-money` before the commits of steps 1–8 (money/PayPal, CLAUDE.md §2; G28) · new files added to the PROJECT.md tree in the same commit (G27) · push the branch after each step · UI steps: 375 px + loading/empty/error states (CLAUDE.md §2).

| # | Commit | Files | Tests first | Verify |
|---|---|---|---|---|
| 0 | ✅ `fix(p2): health check without the database` | done in `7d63adb` | — | production idle at 11:05 UTC |
| 1 [5a] | `feat(p2): payments timestamps + capture attempts` | `backend/db/models.py` (`Payment.created_at`, `updated_at` with `onupdate=now()`, `capture_attempts`), `versions/0003_payments_timestamps.py` | `test_schema.py`: defaults set, `updated_at` changes on status update | on `dev`: upgrade → downgrade → upgrade; `alembic check` clean |
| 2 [1] | `feat(p2): split function (FR-4)` | `backend/core/split.py` (`split_payment`, `SplitError(code)`, `resolve_request`) | `tests/unit/test_split.py`: Hypothesis — sum, cap, only-selected, order (no later invoice gets money while an earlier one has balance), deterministic, ties by `issued_on` then `id`; examples $5,000 → 350000/150000, $8,420 → all, $0.05, exact balance, each error code; `resolve_request` 4 default cases | `pytest -q tests/unit/test_split.py` |
| 3 [2] | `feat(p2): statement read model + api (FR-3)` | `services/statement.py` (D2, D3), `routes/statement.py` (`GET /statement/{token}`, `POST /preview`), `routes/config.py` (`GET /api/config`, D12), `core/logging.py` (uvicorn access-log token filter, D14), routers in `main.py` | `tests/integration/test_statement_api.py`: seed → owed 842000, past_due 350000, oldest 48, 5/5/2, KEPT lines carry invoice_no, OUT lines due_back; unknown / not-ready demo token → 404; preview 4 default cases + 422 codes + other buyer's invoice → `INVOICE_NOT_OPEN`; `remaining_after`; `demo_login` only for demo buyers in DEMO_MODE; one query per table (statement count ≤ ~8); access-log filter (caplog); `GET /api/config` → `{paypal_client_id, demo_mode}` only; app logs carry `token_hash[:8]`, never the token (D14, G21) | `pytest -q tests/integration/test_statement_api.py` |
| 4 [3] | `feat(p2): paypal orders client [apimatic]` | ContextMatic lookups first (CreateOrder body: items/breakdown/custom_id/invoice_id/experience_context/application_context; CaptureOrder: Prefer, response, error issues; GetOrder) → `backend/paypal/orders.py` (D9, D10, R3 mapping); `PayPal.request(timeout=, retries=)` + `PayPal.ensure_token()` (R7); `docs/APIMATIC.md` rows | `tests/unit/test_orders.py`: items sum = amount = item_total, custom_id/invoice_id, NO_SHIPPING, funding mapping (paypal/paylater/credit/venmo/card/unknown), Request-Id keys incl. `-cap-<uid>-<n>`, capture: no retries + 10 s, issue-based mapping of every D10 issue | `pytest -q tests/unit` |
| 5 [4] | `chore(p2): sandbox smoke` | `scripts/sandbox/p2_smoke.py` (D22): demo copy on dev (P1 service) → split $5,000 → `orders.create` → print the `approve` link, or the `payer-action` link if PayPal returns no `approve` link → **Bhargavi approves** → `orders.capture` → `invoices.record_payment` per split → GET each invoice → print ours vs PayPal due; second run with guest card; try decline replay (R2) if PayPal's negative testing allows; redacted fixtures (P1 rules + capture/payer ids) | — | output in PROJECT.md; checks 12, 13 answered |
| 6 [5b] | `feat(p2): order + capture + finish_capture [apimatic]` | `services/payments.py` (D4–D6, D8, R1, R2, R7, R8) | `tests/integration/test_payments_flow.py` (fake Orders on the step 5 fixtures, extending `tests/paypal_fake.py`): happy capture → allocations + paid_cents + ledger_status; double capture → same receipt, nothing new; **`test_concurrent_captures_serialized`** (two full-amount orders at once → one CAPTURED, other VOIDED + 409, no second capture call; R1 GET allowed); balance changed → resplit / 409; ORDER_ALREADY_CAPTURED → finish; declined → 402, CREATED, next attempt `-cap-<uid>-1` (R2); timeout + GET COMPLETED → finish; R1 timed-out then balances changed → GET COMPLETED → finish + credit, never VOIDED; R7 slow capture → 503, lock released; R8 PENDING → CAPTURE_PENDING (repeat → 200), 201 with DECLINED → 402; amount/currency mismatch → unapplied credit + ERROR log; every write's Request-Id; **order**: lock + payment CREATED + planned_split, PayPal create failure → payment FAILED + 503/502 (D4, G16); repeat `/capture` on VOIDED → 409 `STATEMENT_CHANGED`, on FAILED → 409 `PAYMENT_FAILED` (D5, G17); audit `payment.captured` (D6, G18); ORDER_NOT_APPROVED → 409, TRANSACTION_REFUSED → 402, DUPLICATE_INVOICE_ID → GET → finish or 502 (D10, G18) | `pytest -q tests/integration/test_payments_flow.py` |
| 7 [5c] | `feat(p2): record allocations + resolve_recording (FR-6)` | `services/payments.py` (D7, R13, D23) | same file: all allocations RECORDING in one txn → gather → one write txn; success → RECORDED + transaction id + PayPal due + no mismatch; 4xx → FAILED; timeout → stays RECORDING; `PAYMENT_AMOUNT_GREATER_THAN_AMOUNT_DUE` → FAILED + mismatch, no retry (D23); `resolve_recording` finds `[MTC:uid]` → RECORDED with no second record call; not found → retry same Request-Id; **12 s overall cap** (slow fake → stays RECORDING, call returns) (D7, G19) | same |
| 8 [5d] | `feat(p2): payment routes + receipt api (FR-5)` | `routes/statement.py`: `/order`, `/capture` (BackgroundTask recording, R14), `GET /payments/{id}`, `POST /payments/{id}/refresh` (R4); receipt builder (D11 incl. `resplit`) | same file, route level: another buyer's order → 404; every D13 code; `/capture` answers before recording; `/refresh` resolves a FAILED allocation once; re-POST after `CAPTURE_UNCONFIRMED` → finish; recording scheduled as a BackgroundTask (asserted at service level — TestClient runs background tasks before returning, G20); **statement after payments**: CAPTURED + CAPTURE_PENDING listed with `applied`, CREATED/VOIDED never listed, notices `PAYMENT_VOIDED` / `RECORDING_PENDING` / `PAYMENT_CREDITED` within 7 days, `credit_cents` (D2, G22); **`tests/integration/test_scoping.py`** (invariant 7): a token never reads, previews, orders, captures or refreshes another buyer's data; not-ready demo buyer → 404 (G23) | `pytest -q` |
| 9 [6] | `feat(p2): money + api client (ts)` | `lib/money.ts`, `lib/api.ts`, `lib/paypal.ts` (SDK loader), `lib/money.test.ts`; `package.json` `"test": "node --test lib/"`; CI: a Node 22 step runs only `npm test`; tsc/lint/build stay on Node 20 (G2) | `lib/money.test.ts`: formatCents, parseDollars (commas, `$`, 2 decimals, too many digits, negative, empty → null) | `npm test; npx tsc --noEmit; npm run lint` |
| 10 [7] | `feat(p2): statement page (FR-3, FR-4)` + `ci: next build` | `app/s/[token]/layout.tsx` (noindex meta), `page.tsx` + client `Statement.tsx` in `<Suspense>` with `useParams()`, `components/` MemoGroup, InvoiceList, PayPanel, StatusChip, Notices, DemoLogin; `ci.yml` adds `npm run build`. Must include (G24): demo-mode **$5,000 prefill** + its line (D15/D20); `createOrder` reads the selection from a **ref** and sends `data.paymentSource`; "Finishing your payment…" after approval (R14); decline → `actions.restart()`; 409 → refetch + message; credit line; tables → cards < 640 px; buttons ≥ 44 px; no Ask box / "Something wrong?" rendered (P4); **DemoLogin's guest-card fallback text** "Login trouble? Use 'Debit or Credit Card' with sandbox card 4111 1111 1111 1111, any future date, any CVV" (R9); **"Check again" button after 503 `CAPTURE_UNCONFIRMED`** that re-POSTs `/capture` (R4) | tsc/lint; manual: uvicorn + `npm run dev` + a dev demo copy; pay $5,000 as the sandbox buyer; 375 px + desktop; skeleton / invalid / all-paid / notices / 409 / decline states; buttons disabled while preview invalid; `npm run build` | local run + `npm run build` |
| 11 [8] | `feat(p2): receipt page` | `app/s/[token]/receipt/[paymentId]/page.tsx`, `components/Receipt.tsx` (D16: polling 2 s ≤ 20 s, "Retry updating PayPal", resplit note, View on PayPal) | tsc/lint; manual after a real payment | local |
| 12 [9] | `feat(p2): landing starts the demo (FR-12)` | `app/page.tsx` + `components/StartDemo.tsx` (D17) | manual: start → statement; progress text "Creating your PayPal sandbox invoices… (~2–5 s)"; 429/503/502 text + Retry; **no "See the supplier side" button until P3** (D17, G24) | local |
| 13 [10] | (no commit) live run | I open the PR (`p2-statement` → `main`; no `gh`, so I give the compare link) once CI is green, then **stop**. Bhargavi runs migration 0003 on production, then says **"merge"**. I merge → Render auto-deploys (check the deploy trigger says "New commit") → live $5,000 payment → **double-click Pay / repeat capture once** (§6, G25) → screenshots → Render logs show no token → Neon `main` idles again afterwards (D24) | — | P2.md §6 |
| 14 [11] | `docs(p2): status, results, apimatic` | PROJECT.md, TRD §9 (2/12/13), BACKEND_SCHEMA (D2 fields, D6 `CAPTURE_PENDING`, D11, D12, D13), APIMATIC.md complete, P2.md §6 ticks, build log; **`docs/img/apimatic-1.png`** from Bhargavi (still open from P1, G26) | — | `/verify` green |

## 3. Gaps and misalignments found while planning
| # | Finding | Severity | Proposed resolution |
|---|---|---|---|
| G1 | Step 2 (statement, D2) needs `payments.updated_at` for notices, but the spec adds it in step 5 (migration 0003). | Ordering | Migration first (plan step 1). |
| G2 | D18 says "`node --test` file, no new framework", but `.ts` tests need type stripping: Node 22.18+ runs `.ts` natively; CI and Render use **Node 20**, which can't. | Blocker for TS tests in CI | **Only the `.ts` tests run on Node 22** (a separate CI job/step with `setup-node` 22). `next build` in CI stays on **Node 20, same as Render** (approved fix). No `render.yaml` change. Local Node is 22.23. |
| G3 | D2 doesn't say whether paid invoices appear in the statement's invoice list. | Spec gap | List every invoice with its chip (Paid / Part paid / Overdue / due in N d); only invoices with due > 0 get a checkbox. |
| G4 | D5 carries one SQLAlchemy session across `await` and two threadpool hops (lock → capture → finish). Sessions aren't thread-safe; sequential use from different threads is fine with psycopg 3 as long as nothing else touches it. | Design note | One coroutine owns it; `try/finally` rollback+close (R7); a test cancels mid-capture and checks the lock is released. |
| G5 | `test_concurrent_captures_serialized` needs two captures really concurrent against Postgres: the second must block on `pg_advisory_xact_lock` while the first awaits PayPal. | Test design | Fake capture sleeps 0.5 s; run both `/capture` service calls with `asyncio.gather` (two sessions, threadpool); assert order of events + one capture call. |
| G6 | D14's access-log filter: uvicorn's access logger is `uvicorn.access`, its record `args` = (client, method, path, version, status). Installed in `main.py` at import, it applies under `uvicorn` on Render. | Detail | `core/logging.py::RedactTokenFilter`, regex `/api/statement/[^/?]+` → `/api/statement/<redacted>`; also `/s/` paths never reach the api. Test with caplog. |
| G7 | D9 says `brand_name` = `SUPPLIER_NAME` and the R3 mapping puts it in `payment_source.paypal.experience_context`; for `venmo` the experience context has fewer fields (no `shipping_preference` in some versions). | Check | Confirm per field with ContextMatic (step 4) before coding; the step 5 smoke proves it live. |
| G8 | D7's BackgroundTask runs in the api process after the response; a Render deploy/restart in that window leaves allocations `RECORDING`. | Accepted risk | P2 recovery = receipt's "Retry updating PayPal" (`/refresh`, R4) → `resolve_recording` finds `[MTC:uid]` first, so no double record; P3 sweep covers it automatically. |
| G9 | D11 `resplit` compares allocations with `planned_split`; for a CAPTURE_PENDING payment there are no allocations yet. | Detail | `resplit=false` and `allocations=[]` while pending; receipt shows "PayPal is still processing your payment". |
| G10 | D15 "buttons disabled while the preview is invalid" — the PayPal SDK has no `disabled` prop; it uses `onInit(data, actions)` + `actions.disable()/enable()` and still renders clickable-looking buttons. | UX detail | Use `onInit` actions + an overlay message; `onClick` returns `actions.reject()` when the preview is invalid (belt and braces). |
| G11 | D17 landing wiring changes `app/page.tsx`, which P5 polishes; `StartDemo` must be a client component while the page stays a server component (CLAUDE.md §6). | Detail | `components/StartDemo.tsx` client component; page stays server. |
| G12 | `lib/paypal.ts` (own loader, review fix 5) must avoid loading the SDK twice when React re-renders / Strict Mode mounts twice in dev. | Detail | Module-level promise cache keyed by the URL. |
| G13 | Smoke script (D22) "if a decline can be simulated": PayPal sandbox negative testing needs a sandbox account flag and special values. | Check | Try the documented negative-testing header/amounts once; if not possible in sandbox, R2 stays covered by the mocked test only — noted in results. |
| G14 | P2 adds no `render.yaml` change — good, because the Blueprint doesn't sync. If one becomes necessary, Bhargavi must Manual Sync. | Process | Flag any `render.yaml` diff in the commit message. |
| G15 | Spec time box ~10 h; this plan estimates **~12.3 h** (§5, after the re-read). With "nothing is cut", the overrun moves P3. | **Over box** | See §5. |
| G16 | D4's "PayPal create-order failure → payment FAILED, 503/502" had no test in the plan. | Missing test | Added to step 6. |
| G17 | D5's repeat-capture cases for `VOIDED` (409 `STATEMENT_CHANGED`) and `FAILED` (409 `PAYMENT_FAILED`) had no test. | Missing test | Added to step 6. |
| G18 | D6's audit row `payment.captured` and D10's `ORDER_NOT_APPROVED` / `TRANSACTION_REFUSED` / `DUPLICATE_INVOICE_ID` paths were only unit-mapped, not tested end to end. | Missing test | Added to step 6. |
| G19 | D7's 12 s overall recording cap had no test. | Missing test | Added to step 7. |
| G20 | FastAPI's TestClient runs BackgroundTasks **before** it returns the response, so "`/capture` answers before recording" can't be observed through TestClient. | Test design | Assert at service level that `/capture` schedules `record_allocations` as a BackgroundTask and the receipt it returns shows allocations not yet recorded. |
| G21 | D14 "app logs use `token_hash[:8]`" was missing from the plan. | Missing | `core/logging.py::token_ref(token)`; statement/payments services log that, never the token; test in step 3. |
| G22 | D2's payments list, notices and `credit_cents` can't be tested in step 3 (no payments exist yet). | Ordering | Tested in step 8 once payments exist. Payments carry `applied` for STATEMENT and OFF_PAYPAL alike (BACKEND_SCHEMA §3 shape). |
| G23 | CLAUDE.md invariant 7 names `tests/integration/test_scoping.py`; the plan spread scoping checks over other files. | Alignment | Create `test_scoping.py` in step 8 with every statement route; P3 adds the supplier/demo-cookie cases to the same file. |
| G24 | Several D15/D17 UI requirements were only implicit in the plan (prefill, ref-based `createOrder`, `paymentSource`, finishing state, decline restart, no supplier button / no Ask box). | Missing detail | Listed explicitly in steps 10 and 12. |
| G25 | P2.md §6 "double-click / repeat capture tried live once" and "Neon idles again" weren't in the live-run step. | Missing | Added to step 13. |
| G26 | `docs/img/apimatic-1.png` is still open from P1 and the claim needs it. | Carry-over | Bhargavi, step 14. |
| G27 | New files not yet in the PROJECT.md tree: `core/logging.py`, `routes/config.py`, `app/s/[token]/layout.tsx`, `components/Statement.tsx` · `Notices.tsx` · `DemoLogin.tsx` · `StartDemo.tsx`, `lib/money.test.ts`, `tests/integration/test_payments_flow.py` already listed. | Process | Add each in the commit that creates it (CLAUDE.md §0). |
| G28 | CLAUDE.md §2: `/review-money` for every money/PayPal change — not stated per step. | Process | Run before the commits of steps 1–8 (the "Every step" line). |

## 4. Open checks in this phase
| Check | When | How | If it fails |
|---|---|---|---|
| 12 capture shape (`purchase_units[0].payments.captures[0].{id,status,amount}`) + `NO_SHIPPING` (no address step) | step 5 smoke | real order approved by Bhargavi, capture with `Prefer: return=representation` | parse what PayPal returns; `application_context` instead (D9) |
| 13 items shown in PayPal's window + which Pay Later appears at $5,000 | step 5 smoke (+ step 13 live) | Bhargavi looks at the PayPal window, screenshot `paypal-popup-split.png` | drop `items`, put the split in `description` (§7 risk) |
| 2 Venmo button renders (`buyer-country=US`) | step 13 live | screenshot or "not shown" | README limits |
Results → PROJECT.md decisions + TRD §9 in the step 14 commit (12/13 already in the step 5 commit).

## 5. Time estimate
| Step | Est. |
|---|---|
| 1 migration 0003 | 0.3 h |
| 2 split + Hypothesis | 0.7 h |
| 3 statement read model + api + config + log filter | 1.4 h |
| 4 Orders client + APIMatic lookups | 0.9 h |
| 5 smoke (with Bhargavi) | 0.7 h |
| 6 order + capture + finish (lock, R1/R2/R7/R8, concurrency test, G16–G18) | 1.8 h |
| 7 record + resolve_recording | 1.0 h |
| 8 routes + receipt api + BackgroundTask + statement-after-payments + `test_scoping.py` | 0.9 h |
| 9 TS money/api/paypal loader + node tests + CI | 0.6 h |
| 10 statement page + CI next build | 2.0 h |
| 11 receipt page | 0.6 h |
| 12 landing | 0.4 h |
| 13 live run | 0.6 h |
| 14 docs + APIMatic | 0.4 h |
| **Total** | **~12.3 h** |

> ⚠️ **Over P2's ~10 h box by ~2.3 h.** Per Bhargavi, nothing is cut: the overrun moves P3's start (P3 then runs Tue night → Wed morning, P4 Wed midday). Biggest levers to win time back without cutting scope: reuse `tests/paypal_fake.py` for Orders (no second fake), build the statement page directly on the API shape (no mock data), and keep step 5's smoke to one PayPal-login run + one guest-card run.

## 6. Build log
| # | What happened | Decision |
|---|---|---|
| B0 | Plan approved by Bhargavi with 5 fixes: G2 (only `.ts` tests on Node 22, build on Node 20), step 5 payer-action link fallback, step 10 guest-card text + "Check again", step 13 open PR and stop until "merge", §1 step numbers. | Written into this plan before Stage 4. |
| B1 | Step 4: ContextMatic `ask` errored twice on capture issue names; `model_search` gave statuses, models and `ErrorDetails.issue`. ContextMatic marks `application_context` deprecated. | Issue names from PayPal's Orders error docs, verified live (INSTRUMENT_DECLINED seen); `application_context` only on the card path. |
| B2 | Step 5 smoke (PayPal login): order `PAYER_ACTION_REQUIRED` + `payer-action` link only; approval spun (no `return_url`) but the order was APPROVED; capture COMPLETED; INV-1038 $0 / INV-1042 $2,000 due on PayPal = ours; mocked decline not replayed for the same key. Items not shown on PayPal's new checkout page. | Checks 12 ✅, 13 ◐ — docs and video claim changed (TRD §9, P2 §0, UI_UX, APP_FLOW). |
| B3 | Step 5 guest-card run (hosted `checkoutnow` link, no payment_source): PayPal forced "Create Account & Pay Now" + SMS code (sandbox code 111111), showed "Purchase complete" with our brand name, but capture failed `COMPLIANCE_VIOLATION`, then `ORDER_NOT_APPROVED`; order has no payment source — **nothing captured**. Script wrongly called the second error a decline replay; fixed (compares the issue). | Judges must never use the hosted link's guest flow. The real fallback is the JS SDK **"Debit or Credit Card"** button on our page (inline form) → test it in step 10; if it fails too, R9's card hint is dropped and the spare sandbox buyer is the only fallback. R2 result stands from the PayPal-login run: no replay. |
