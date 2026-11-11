# Phase P3 — Build plan (how we'll execute `P3.md`)

Spec: `docs/phases/P3.md` v3 (source of truth). This file is the working plan: order, dependencies, checks, and the gaps found while planning.
Branch: `p3-supplier`, rebased on `main` @ `718bce9` (P4 merged as PR #5; this plan as PR #6). **Approved by Bhargavi 2026-11-11 with all G1–G22 as proposed** (+ G23–G25 from the re-read). FRs: FR-10 (rest), FR-11, FR-14, FR-15 (copy link), FR-12 (entry points) in **P3a**; FR-7, FR-1 (endpoint), FR-10 reminders in **P3b**. Open check closed: **#3** (webhook to Render). New sandbox facts to prove: PUT to due $0 (b0), remind call shape (b4), webhook verify with the raw body (b1).
Read for this plan: P3.md (all), BACKEND_SCHEMA §2/§3/§8 (incl. Bhargavi's uncommitted §2 auth edits), TRD FR-1/7/10/11/14/15 + §6b–6b-5 + §9, UI_UX §4.3–4.5, APP_FLOW J1/J6/J8, CLAUDE.md §3–§6, and the code: `services/proposals.py`, `services/payments.py` (`record_allocations`, `resolve_recording`, `_record_one`, audit refs), `services/statement.py` (credit, requests), `agent/facts.py` (credit), `services/demo.py` (`read_demo_cookie`), `routes/supplier.py`, `db/models.py`, `render.yaml`, `requirements.txt`.

## 1. Who does what, and what blocks what
| Bhargavi | Needed by |
|---|---|
| **Merge the open P4 PR** (`p4-agent` → `main`, up to `3957ca7`) — G1 | before a6 (the P3a PR must diff cleanly) |
| Commit or let me commit the spec: `docs/phases/P3.md` is untracked and `APP_FLOW.md` / `BACKEND_SCHEMA.md` have your uncommitted §2 edits on `p4-agent` — G2 | step a1 (they're the spec I build from) |
| Answer the gaps marked **[decide]** in §3 | before "approved" |
| Live checks a6 (judge path on production, screenshots) | after the P3a PR merges |
| **P3b only:** sandbox webhook (URL + 6 events) → `PAYPAL_WEBHOOK_ID` on the api service; Render Blueprint sync after the worker lands + re-type the worker's secrets ($7/mo) | b1 live, b2 live |
| P3b live check b5 (pay one invoice on PayPal's own page) | after the P3b PR merges |

Everything in a1–a5 and b0–b4 runs locally with fakes (fake PayPal, fake LLM, test Postgres); only a6/b5 and three sandbox probes (b0 PUT-to-$0, b1 verify, b4 remind) need the real sandbox.

## 2. Steps (one commit each; tests first for money/scoping/idempotency; ruff + pytest + tsc + eslint + `next build`; `/review-money` before money commits; push after each)
### P3a — the judged path (ship before any P3b work)
| # | Commit | Build | Tests first / verify |
|---|---|---|---|
| a0 | `docs(p3): spec v3 + build plan` | commit P3.md + the APP_FLOW/BACKEND_SCHEMA edits + this plan (after "approved", with resolutions folded in) | — |
| a1 | `feat(p3): supplier scope + overview/buyer/proposals api (FR-11)` | `routes/supplier_scope.py` dependency `supplier_scope()` → `Scope{kind: admin\|demo, buyer_ids?}` (A1: Bearer ADMIN_TOKEN, else `mtc_demo` cookie → that session's `demo_ready` buyers; neither → 401 `UNAUTHORIZED`; DEMO_MODE off + no admin → 401); `services/supplier.py`: `overview(scope, today)` (A2), `buyer_detail(scope, id)` (A3), `proposal_cards(scope, statuses)` (A5, `suggested` label only); `GET /api/demo/whoami` (A10, G13); `decide` route gets `supplier_scope` (scoped 404) | `tests/integration/test_supplier_api.py`: shapes from a demo copy (owed, past due, paid this week = NY Mon–Sun G10, sample rows not in API); `test_scoping.py`: other session's buyer/proposal → 404; admin **lists** exclude demo copies, admin **acts** on a demo id; no cookie/no token → 401; `/demo/whoami` returns only the cookie's own token, null without one |
| a2 | `feat(p3): decide — credit, edits, replies (FR-10)` | `services/proposals.py`: `mode` param; **no mode → decided under the lock** (due ≥ amount → APPLY, else CREDIT); explicit APPLY with due < amount → 409 `NEEDS_CREDIT`; `SPLIT` → 422 `NOT_YET`; effective values written to `payload` (+ `payload.original`) **under the lock before any PayPal call** (R1); `_finish` merges `result`; CREDIT → DONE, `payload.credit_cents`, audit `proposal.credited`, no PayPal call; edits (A7: ADJUSTMENT amount/reason re-validated vs line price; REPLY/RETURN_CHECK text ≤ 1000) **only on PENDING** (G7); REPLY/RETURN_CHECK/PROMISE_NOTE approve (A8, P3a scope) + REJECT with reply text; **credit sources** = Σ unapplied + Σ DONE CREDIT proposals in `statement.py` **and** `agent/facts.py` (G5); `requests[]` gains `reply` + credited/applied wording (A11) | `test_proposals.py` (spec a2 list) **+**: the P4 test "due < amount → 409" becomes "explicit APPLY → 409; no mode → CREDIT" (G4); FAILED can't be edited or rejected, only retried (G7); agent Facts/validator accept the credit figure (G5); `/review-money` |
| a3 | `feat(p3): record off-PayPal payment (FR-14)` | `POST /supplier/payments/record` (A9): `client_uid` → `payments.uid`; repeat with the same uid **for the same buyer** → existing payment, a different buyer → 409 `CLIENT_UID_REUSED` (G8); `received_on` → `received_at` (NY noon, G9); `record_allocations` generalised (method/date/note from the row, `[MTC:<uid>] Cheque <ref>`); `resolve_recording` accepts RECORDED | `test_payments_flow.py`: spec a3 list + uid-reuse across buyers + `received_on` in the future → 422; the RECORDED payment shows on the statement; `/review-money` |
| a4 | `feat(p3): payment detail api (FR-11)` | `GET /supplier/payments/{id}`: payment, allocations, stored `paypal_*`, timeline from `audit_log` (ref = payment uid **or** `detail.payment_id` = id — G11) | scoping, timeline order, stored values, no PayPal call (fake asserts 0 calls) |
| a5 | `feat(p3): supplier ui` | `app/supplier/page.tsx`, `buyers/[id]`, `payments/[id]` (client components, `useParams` in `<Suspense>` like P2); `components/supplier/{ProposalCard,SampleRows,RecordPaymentModal,SupplierBanner}.tsx`; split preview in the modal via the **existing** `POST /statement/{token}/preview` using the buyer's token from `buyer_detail` (G12 — no new endpoint); "See it as Demo Diamond Co." on statement + receipt (shown only when `/demo/whoami` token = page token); landing "See the supplier side"; no cookie → `router.replace("/")` (G14); FAILED card: error + Retry, no Edit/Reject (G7) | tsc/lint/build; local run through the proxy; 375 + 1280 px (Bhargavi's browser check, as in P4) |
| a6 | (no commit) live P3a | PR → CI green → Bhargavi merges → Render; judge path on production; screenshots `supplier-1.png`, `proposal-approved.png`, `paypal-invoice-adjusted.png` | P3.md §6 rows 1–4 |

### P3b — reliability and completeness (after P3a is live; order fixed by R12)
| # | Commit | Build | Tests first / verify |
|---|---|---|---|
| b0 | `feat(p3): moved-from-P3a items` | **sandbox probe first**: PUT bringing a part-paid invoice to due $0.00 (record in CLAUDE.md §4); SPLIT; RETURN_CHECK `CREDIT_LINE` (OUT → RETURNED; KEPT → new PENDING ADJUSTMENT) / `ASK_FOR_SLIP`; PROMISE_NOTE date edit; A4 live fetch with the in-flight guard | tests per item; if the $0 PUT fails in sandbox → SPLIT = "credit the whole amount" + note (G15) |
| b1 | `feat(p3): paypal webhook (FR-7)` | `routes/paypal_webhook.py` + `services/webhooks.py` (B1–B3): raw-body verify, store, 200, BackgroundTask → process + scoped sweep; events for invoices/orders we don't have (dev/prod share one sandbox app) → stored, marked processed, ignored (G16); APIMatic lookup for verify-webhook-signature + `docs/APIMATIC.md` row (G17) | `test_webhooks.py` (spec b1 list + unknown-invoice event ignored) |
| b2 | `feat(p3): sweep + worker` | `services/sync.py::sweep` (B4 **+ CAPTURE_PENDING payments: GET capture → `finish_capture` when COMPLETED**, G6; "CREATED > 3 h → GET order, COMPLETED → finish else VOIDED" — our statuses have no APPROVED, G6); `backend/worker.py` adaptive 30 s / 60 min; `render.yaml` worker + `PAYPAL_WEBHOOK_ID` declared on the api (G18) | sweep tests per item; interval unit test with a fake clock; Bhargavi: Blueprint sync, secrets, logs show "sleeping 60 min" |
| b3 | `feat(p3): import endpoint + page (FR-1)` | B7 + `python-multipart` in requirements (not there yet — confirmed) | `test_import_api.py` |
| b4 | `feat(p3): reminders (Job C)` — last | B6: `agent/prompts/reminder.md` (versioned), `ReminderDraft` + validator; remind call (sandbox probe for body shape first); demo "Draft a reminder" button | fake-LLM tests; no eval rows for Job C → not part of any claim (G19) |
| b5 | (no commit) live P3b + open check 3 | pay one invoice on PayPal's page → statement updates within a minute | P3.md §6 rows 5–6 |
| b6 | `docs(p3): status, results` | B8 (minus items already done in P4 step 10 — G20) | `/verify` |

## 3. Gaps — resolutions approved 2026-11-11 (as proposed unless noted)
**Notes on approval:** G4 per P3 A6 (no mode → decided under the lock; explicit APPLY with due < amount → 409). G5 one credit helper for statement, overview and the agent's facts/validator, with the equality test. G16 store-and-ignore. G17 if ContextMatic doesn't cover webhook verification, log the coverage gap like Invoicing. G21 as proposed. **G3: order P3a → P5 essentials → P3b; no pushes to `main` after Thu Nov 12 15:00 ET.**
| # | Finding | Proposed resolution |
|---|---|---|
| G1 | **Blocker:** the last P4 PR isn't merged (`main` = `08c3ccb`; `p4-agent` = `3957ca7`). | Bhargavi merges it first; `p3-supplier` is cut from `p4-agent` so nothing is lost either way. |
| G2 | The spec isn't committed: `P3.md` is untracked; your BACKEND_SCHEMA §2 / APP_FLOW J1 edits are uncommitted on `p4-agent`. | Commit them with this plan in a0 (on `p3-supplier`), after review. |
| G3 | **Time [decide].** Realistic estimate (§5) is **~7.5 h for P3a and ~8 h for P3b** vs the 3 h + 3.5 h box; hard deadline **Thu 15:00 ET**, target Wed 23:00 ET, and P5 (README/video/submission) isn't started. | Proposed order: **P3a → P5 essentials (README, video, submit) → P3b** in R12 order. Nothing cut, but P3b would land after the target submit if time runs out. Your call. |
| G4 | A6 changes P4's tested behaviour: today "no mode + due < amount" → 409 `NEEDS_CREDIT`; P3 says → CREDIT. P4 docs (P4.md Z4/§8, BACKEND_SCHEMA §2 decide row + error list, test `test_needs_credit_when_the_invoice_is_already_paid`) say 409. | Update the test (explicit APPLY → 409; no mode → CREDIT) and the docs in a2; P4 records stay as history. |
| G5 | A6 redefines credit (Σ unapplied + Σ DONE CREDIT proposals) but only names the statement/overview. The agent's Facts (`facts.py`) and the validator's allowed money set use `credit_cents` too — the agent would contradict the statement, or a correct "credit $20.00" answer would fail validation. | One helper `buyer_credit_cents(session, buyer_id)` used by statement, overview **and** `facts_for_buyer`; test that Facts = statement after a CREDIT approval. |
| G6 | B4 says "CREATED/APPROVED > 3 h → VOIDED" — our payment statuses have no APPROVED (that's PayPal's order status), and B4 omits **CAPTURE_PENDING** payments (P2 D6 waits for the webhook; if it never comes, nothing finishes them). | Sweep: CREATED > 2 min → GET order (COMPLETED → `finish_capture`; > 3 h and not captured → VOIDED); CAPTURE_PENDING → GET capture → `finish_capture` on COMPLETED. |
| G7 | Edits/REJECT on a **FAILED** ADJUSTMENT: the ledger line is already written; editing the amount or rejecting would leave ledger ≠ PayPal. Spec doesn't say. | Edits and REJECT only on PENDING (409 `NOT_PENDING`); FAILED → Retry only (re-sends the payload amount, R1). UI hides Edit/Reject on FAILED. |
| G8 | A9 idempotency by `client_uid`: a uid replayed for **another buyer** (or another demo session) would return someone else's payment. | Lookup by uid **and** buyer (and scope); same uid, different buyer → 409 `CLIENT_UID_REUSED`. |
| G9 | A9 `received_on` is a date; `payments.received_at` is a timestamp (P2 uses `ny_date`). | Store 12:00 New York time on that date (so `ny_date` gives it back on any server TZ); future dates → 422. |
| G10 | A2 "Paid this week = NY week" — which day starts it? | Monday 00:00 New York (ISO week); stated in BACKEND_SCHEMA §3 overview shape. |
| G11 | A4 timeline: audit rows use `ref = payment uid` for payment events but `ref = allocation uid` (+ `detail.payment_id`) for recordings; "webhook received" has no audit yet. | Query both forms; b1 adds `webhook.received` with `detail.payment_id` when it maps to a payment. |
| G12 | A12 "live split preview via the P2 preview logic" — the only preview endpoint is token-based (`/statement/{token}/preview`). | Reuse it with the buyer's statement token (the supplier page already has `statement_url`); no new endpoint. |
| G13 | `/demo/whoami` isn't in BACKEND_SCHEMA §2. | Add the row in a1: `GET /demo/whoami` (demo cookie) → `{statement_token \| null}`. |
| G14 | A1 "`/supplier` with no cookie → redirect to `/`" — the pages are client-rendered (Cache Components/PPR), a server redirect needs `cookies()` and would make the route dynamic. | Client side: the overview call returns 401 → `router.replace("/")`. |
| G15 | b0's SPLIT depends on an unproven PayPal behaviour (PUT to due exactly $0). | Probe first; if PayPal refuses, SPLIT falls back to CREDIT of the whole amount and the docs say so. |
| G16 | Dev and production share one sandbox app (P1): production's webhook will receive events for dev invoices/orders. | Unknown `paypal_invoice_id` / `custom_id` → store the event, mark processed `ignored: unknown`, no error. |
| G17 | CLAUDE.md §4b: PayPal calls need an APIMatic lookup + `docs/APIMATIC.md` row. Webhook verify (Notifications API) and Orders/Captures GET for the sweep may or may not be in ContextMatic's context; Invoicing (remind, PUT) isn't. | Look up in b1/b2; rows for what it covers, "not covered" note for the rest (like P1 D23). |
| G18 | `PAYPAL_WEBHOOK_ID` is in `config.py` but not declared in `render.yaml`. | Declare it on the api service (`sync: false`) in b2 with the worker — one Blueprint sync for both. |
| G19 | B6 Job C uses the LLM but has no eval rows; CLAUDE §5 says prompt changes re-run the eval. | Fake-LLM tests + validator only; Job C isn't part of any README claim; noted in P3 results. |
| G20 | B8 lists doc items already done in P4 step 10 (CLAUDE §4 "SENT", BACKEND_SCHEMA `NEEDS_CREDIT`). | b6 does only the remaining ones. |
| G21 | **[decide]** A8 lets an empty REPLY approval mean "Demo Diamond Co. has seen this", and A12 says the UI asks for a text. Should the API require text for REPLY approve? | API: text optional (BACKEND_SCHEMA §8); UI: text required with a "Mark as seen" secondary button that sends none. |
| G23 | (re-read) A6 lets the supplier choose CREDIT explicitly even when the invoice still has enough due (e.g. a goodwill credit). | Allowed: explicit `mode: CREDIT` always works on a PENDING ADJUSTMENT; the card offers it under Edit. |
| G24 | (re-read) A11's buyer wording ("Approved as $20.00 credit on your account" vs "Approved — $20.00 off INV-1051") depends on `payload.mode`; `agent/triage.py::summary()` only knows the kind. | `summary()` takes the payload's `mode`/`credit_cents`; requests read it at read time (no stored text). |
| G25 | (re-read) Time and deadline. | P3a planned at **~2 h** (Bhargavi: my estimates ran ~4× high in P4); no pushes to `main` after Thu 15:00 ET. |
| G22 | A11: the statement's "Credit $X" line exists (P2) but uses payments only; after G5 it includes approved credits. The receipt's `credit_cents` is per payment and stays so. | Only the statement/overview total changes; receipt unchanged. |

## 4. Open checks in this phase
| Check | When | How |
|---|---|---|
| #3 webhook to Render (real delivery verifies) | b5 | pay one invoice on PayPal's page; api logs show `webhook.received` verified; statement updates |
| PUT to due $0.00 on a part-paid invoice (new) | b0, first | `scripts/sandbox/p3_probes.py put0` on a dev copy |
| Remind call body + 2/day limit (new) | b4, first | `p3_probes.py remind` |
| Raw-body webhook verify (new) | b1 | PayPal's "Webhooks simulator" event to the Render URL after the b1 deploy |

## 5. Time (estimate vs P3.md's box)
| Step | Estimate |
|---|---|
| a1 scope + 3 read APIs + whoami | 1.5 h |
| a2 decide: modes, payload-effective, credit, edits, replies, credit helper | 1.75 h |
| a3 record off-PayPal | 1.0 h |
| a4 payment detail | 0.5 h |
| a5 supplier UI (3 pages, card, modal, entry points) | 2.25 h |
| a6 live | 0.5 h |
| **P3a** | **~7.5 h** by step — **plan on ~2 h** (G25: P4 ran ~4× under my estimate) |
| b0 moved items + PUT probe | 1.5 h |
| b1 webhooks incl. D23 reconcile | 2.0 h |
| b2 sweep + worker + render | 1.5 h |
| b3 import | 0.75 h |
| b4 reminders | 1.25 h |
| b5 live + b6 docs | 1.0 h |
| **P3b** | **~8 h** (box 3.5 h) |
| **Total** | ~15.5 h by step → ~4 h at the P4 ratio; order P3a → P5 essentials → P3b (G3) |

## 6. Build log
| # | What happened | Effect |
|---|---|---|
| B1 | a1–a2 built. a2 changed two P4 behaviours on purpose: approve with no mode on a paid invoice is now a CREDIT (P3 A6/G4; explicit APPLY still 409 — test updated), and non-ADJUSTMENT approvals go to DONE (A8; RETURN_CHECK needs a reply text in P3a). Credit rule `services/credit.py::credit_from` is pure so the statement keeps its query budget (8); `buyer_credit_cents` wraps it for the agent Facts. | Docs: BACKEND_SCHEMA §2 decide row, §3 credit + requests.reply, §8 ADJUSTMENT payload/result. |
| B2 | a3 built. The receipt lookup (also used by `/refresh`) now accepts RECORDED off-PayPal payments, so a failed cheque recording is retried by `/refresh` (R3); the receipt UI shows "updating PayPal" for RECORDED as for CAPTURED. Off-PayPal payments are stored at 12:00 New York on `received_on` (G9). | BACKEND_SCHEMA §2 record + receipt rows. |
