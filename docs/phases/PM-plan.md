# Phase M — Multi-buyer demo: build plan

Spec: `docs/phases/P3.md §M` (M1–M8, Bhargavi, Nov 11). Seed data: `IMPLEMENTATION_PLAN.md §Seed`. Flow: `APP_FLOW.md` J1 steps 2 and 7. Shapes: `BACKEND_SCHEMA.md §2` (`/demo/whoami`). Screen: `UI_UX.md §4.3`.
Branch: `p3m-buyers` from `main` @ `1a1de13` (P3a merged, PR #9). FRs: FR-12 (demo), FR-3/FR-15 (statement and link per buyer), FR-4/FR-5 (pay), FR-14 (record), FR-11 (overview).
Order: **M before P3b** (`P3b.md §3` already says how P3b builds on M).
Read for this plan: `services/demo.py` (seed, start, finish), `services/mirror.py` (per-buyer, Semaphore(5)), `services/importer.py` (buyers keyed by email), `services/supplier.py` (scope, `whoami`, ordering), `agent/facts.py` (`facts_from_seed` uses `build_csv`), `components/{AskBox,MessageBox,PayPanel,SeeAsSupplier}.tsx`, `components/supplier/*`, `tests/integration/test_demo.py`.

## 1. Who does what
| Bhargavi | When |
|---|---|
| Review this plan; answer the **[decide]** gaps; say "approved" | before m1 |
| Commit your own `docs/phases/P3b.md` and your P3.md "Moved" lines (still uncommitted; I left them out of my commits) | any time |
| Merge the M PR after CI is green; then the Render timing (m6) runs | after m5 |

## 2. Steps (one commit each; tests first; ruff + pytest + tsc + eslint + `next build`; push after each)
| # | Commit | Build | Tests first / verify |
|---|---|---|---|
| m0 | `docs(pm): multi-buyer spec + plan` | P3 §M, IMPLEMENTATION_PLAN §Seed, APP_FLOW J1, UI_UX §4.3, BACKEND_SCHEMA whoami, TRD FR-12, PROJECT decision line, this plan | — |
| m1 | `feat(pm): three-buyer demo seed (M1)` | `services/demo.py`: buyer data as a list (Northside unchanged); `build_csv(D, email)` = Northside only, byte-identical (G2); `build_demo_csv(D, email)` = all three, emails plus-addressed (G1) | `test_importer.py`: `build_csv` output equals a golden hash taken before the change · `build_demo_csv` imports cleanly → 3 buyers with owed 842000 / 230000 / 596000, oldest 48 / 12 / 31, past due 350000 / 0 / 350000, out/kept/returned 2-5-5 / 1-2-2 / 1-4-1 · unique memo, SKU and invoice numbers |
| m2 | `feat(pm): demo start mirrors all three buyers in parallel (M2, M5)` | `start_demo`: import with `build_demo_csv`; `asyncio.gather` of `mirror_invoices` for the three buyers inside the existing 20 s deadline; any buyer failing → the start fails (503/502 as today); `_finish` sets `demo_ready` on all three in its one transaction; the seed message/proposal and triage stay Northside-only | `test_demo.py`: start → 3 ready buyers in one session, every invoice with a PayPal id + payer link, PayPal create+send called for all 7 invoices · one buyer's PayPal failing → no cookie, none of the 3 reachable · failed start then retry → exactly 3 ready buyers, the failed copy's buyers 404 (G9) · one proposal per start, on Northside · **local timing: 5 starts against the real sandbox, report p50/p95** (`scripts/demo_timing.py`, prints ms only) — if local p95 > 6 s, stop and report (G10) |
| m3 | `feat(pm): scope, whoami, ordering (M3–M5)` | `whoami` → `{statement_token (Northside, by name — G3), buyers:[{id,name,statement_token}]}`; overview buyers ordered by owed desc, then name (G8) | `test_supplier_api.py`: overview lists 3 buyers in order Northside, Lakeside, Harbor with the m1 numbers; header totals 1668000 / 700000 · another session → 404 on each of the 3 buyer pages, statements unaffected, its overview lists only its own 3 · admin lists exclude all demo buyers · whoami returns Northside's token + 3 buyers · `test_record_payment.py`: a cheque of $2,000 on Harbor → INV-1055 $1,450, INV-1058 $550 (oldest first); over-amount on Harbor → 422 with nothing recorded · Lakeside credit: full cheque then approve nothing (no proposal) — credit math unchanged |
| m4 | `feat(pm): every demo buyer real in the UI (M3, M4)` | remove `SampleRows`/`SampleCards`; buyer-page banner links to **that** buyer's statement (`d.statement_token`); overview banner → Northside; `SeeAsSupplier` shows when the token is in `whoami.buyers` and links to that buyer's page (G4); demo chips per buyer (G6) | `tsc`, eslint, `next build`; unit test for the chips helper (Northside's chips unchanged) |
| m5 | (no commit) browser check (M8) | headless Chromium via a throwaway Playwright script in my scratchpad (not a project dependency, G16) against local api + web on the dev database, real sandbox: 375 and 1280 px — overview, each buyer page, each statement link (Copy → open), record a cheque on Harbor → split and "Marked paid"; screenshots to you | screenshots + the script output; no horizontal overflow at 375 |
| m6 | PR → CI → your merge → **Render timing** | 5 demo starts on production, report p50/p95 from the API's `ms` (G10) | p95 > 6 s → stop and report (the PR can be reverted to one buyer in one click) |
| m7 | `docs(pm): status + timings` | PROJECT.md status/decisions with the measured timings; P3 §M results | — |

## 3. Gaps (proposed resolutions)
- **G1 Buyer emails [decide].** The importer keys buyers by email (P1 D2), so the three buyers need three emails. Proposal: plus-address the sandbox buyer (`<local>+harbor@<domain>`, `<local>+lakeside@<domain>`). The invoice recipient doesn't matter for paying: our Pay button is a checkout order that any sandbox buyer approves, and `send_to_recipient=false` means no email is sent. Unproven: that PayPal accepts a plus-addressed recipient. The first m2 sandbox run proves it. Fallback: `harbor-gems@example.com` style addresses.
- **G2 Eval reproducibility.** `agent/facts.py::facts_from_seed` (the eval's Facts) is built from `build_csv`. Changing it would silently change the frozen eval's inputs. So `build_csv` stays Northside-only and byte-identical (golden test); the demo uses a new `build_demo_csv`.
- **G3 whoami picks the newest buyer.** It orders by `id DESC` and would return Harbor or Lakeside's token. The landing "See the supplier side" and the overview banner would then go to the wrong statement. Fix: `statement_token` = the copy's Northside buyer (by name), plus the new `buyers` list.
- **G4 "See it as Demo Diamond Co." only matches one token.** On Harbor's statement it would hide. Fix: show it when the statement's token is in `whoami.buyers`, linking to that buyer's supplier page (today: `/supplier`).
- **G5 Banners.** Overview banner → Northside's statement (as today). Buyer page banner → that buyer's statement (M4).
- **G6 Demo chips are Northside's records [decide].** Ask chips ("Why is S-102 on my bill?", "What do I owe for M-207?") and message chips ("S-104 has a chipped stone…", "I returned S-103 last week") name Northside SKUs and memos. On Harbor's statement the agent would correctly say those don't exist, which looks broken. Proposal: chips per buyer, chosen by buyer name in demo mode (a frontend constant, like the old sample rows): Harbor "Why is S-131 on my bill?" / "What's still with me?" and "S-130 has a chipped stone, please take $50 off" / "I returned S-132 last week"; Lakeside the same with S-142 / S-140 / S-152. Unknown buyer → no chips. Northside's chips are unchanged.
- **G7 Agent on Harbor/Lakeside.** It runs on their own Facts (same code, same validators, caps unchanged), but the eval covers Northside only. No README claim about the other two. The demo-start triage stays Northside-only (one LLM call per start, as today).
- **G8 Ordering.** Name order would put Northside last. Proposal: owed desc, then name, so Northside ($8,420) comes first, then Lakeside ($5,960), then Harbor ($2,300). Admin uses the same order.
- **G9 "A retried demo start makes no duplicates" [decide].** Each start is a new session by design (D17). Two successful starts are two copies, and the supplier view shows only the newest. Proposal: test (a) a start that fails part-way (one buyer's PayPal fails) leaves none of its three buyers reachable, and the retry gives exactly three ready buyers; (b) the mirror's resume path (search + adopt, already in `test_mirror.py`) holds per buyer, so no duplicate PayPal invoices. Tell me if you meant something else.
- **G10 Render timing needs a deploy [decide].** Render deploys only `main`, so the 5 Render runs can only happen after the merge. Proposal: measure locally against the real sandbox first (m2) and stop if local p95 > 6 s. Otherwise merge, measure on Render (m6), and if Render p95 > 6 s, report straight away. Reverting the PR is the one-click way back. Today's Render starts were 2.2–3.4 s (P4 step 9) for 3 invoices. M makes 7 invoices in parallel, about 14 PayPal calls, up to 15 at once.
- **G11 PayPal concurrency.** Three buyers × Semaphore(5) → up to 15 PayPal calls at once (7 creates, then 7 sends). Sandbox throttling would show as a transient error → 503 "try again", nothing reachable. If the timing runs show 429s, use one shared Semaphore(5) for the whole start (slower but gentler) and re-measure.
- **G12 Demo limits.** Unchanged (15 starts/h per IP, 60/h global); PayPal calls per start go from 6 to 14.
- **G13 Pay prefill.** Demo mode prefills $5,000 only when owed ≥ $5,000. Lakeside ($5,960) gets it (clears INV-1040, $1,500 on INV-1056); Harbor doesn't (default = pay all). No change.
- **G14 Atomic readiness.** Each buyer's mirror result is written in its own transaction (as today, per buyer). `demo_ready` for all three is set together in `_finish`, after all three mirrors succeed. A copy is never half-reachable.
- **G15 Numbers in later material.** Overview totals become Open $16,680 / Past due $7,000. The README, video and a6 screenshots aren't made yet, so nothing to change; the a6 screenshots come from the three-buyer build.
- **G16 Browser check (M8).** This session has no browser tool. Headless Chromium is installed (Playwright cache), so m5 uses a throwaway Playwright script outside the repo (no new project dependency). It can't sign into the PayPal sandbox popup reliably, so "pay on PayPal" per buyer is covered by the integration tests (fake PayPal) and the m6 Render runs (real invoices). The browser check does what M8 lists: buyer pages, statement links, record on Harbor.
- **G17 `seed/demo_seed.py`** prints one buyer id; it will print all three (ids and ms only, never tokens).
- **G18 Tests whose expectations change on purpose:** any test that assumes one buyer per demo copy (e.g. `overview["buyers"][0]` = Northside, `buyer_ids[0]`) is updated to pick Northside by name. Each is listed in the commit message; none is weakened.

## 4. Open checks
- G1 plus-addressed recipient accepted by PayPal: first sandbox start (m2).
- Demo-start p95: local (m2), Render (m6); threshold 6 s.

## 5. Time
| Step | Estimate |
|---|---|
| m0 docs + plan | done |
| m1 seed | 20 min |
| m2 parallel start + local timing | 30 min |
| m3 scope/whoami/order + tests | 25 min |
| m4 UI | 25 min |
| m5 browser check | 20 min |
| m6–m7 PR, Render timing, docs | 15 min + your merge |
| **Total** | **~2 h 15 min** |
P3b stays next (`P3b.md`: last merge by Thu 13:30 ET).

## 6. Build log
- **B1 m1** `feat(pm): three-buyer demo seed (M1)` — `build_demo_csv`; `build_csv` byte-identical (golden sha). Importer + unit tests green.
- **B2 m2** `feat(pm): demo start mirrors all three buyers in parallel (M2, M5)` — `_mirror_copy` = `gather` of `mirror_invoices` per buyer, merged result; whole copy ready in `_finish`; whoami → Northside by name (G3, pulled forward from m3 to keep the suite green). Full suite green (548 + 27 demo).
- **B3 local timing gate — STOPPED (p95 > 6 s).** Same machine, same dev DB, real sandbox, 5 runs each, server `ms`:
  | Code | runs (ms) | p50 | p95 (= max at n=5) |
  |---|---|---|---|
  | M (7 invoices, 3 buyers in parallel) | 5,301 · 4,560 · 4,765 · 8,276 · 9,826 | 5,301 | 9,826 |
  | `main` (3 invoices, baseline) | 6,383 · 5,599 · 5,918 · 5,382 · 5,645 | 5,645 | 6,383 |
  No 429s. Per-call breakdown: in M runs 1–3 the slowest create/send were ≤ 1.5 s / ≤ 2.1 s; in runs 4–5 single PayPal calls took up to 4.0 s (create) / 4.1 s (send) — the sandbox spread 7 parallel creates out like a queue. Run 1 fetched the OAuth token 7 times at once (cold-cache stampede, ~1.1 s). The one-buyer baseline is itself over 6 s locally (Render did 2.2–3.4 s in P4 step 9): locally the parallel model triage (Neon + Meta API from this machine) sets the floor. Waiting for Bhargavi before any design change.
- **B4 decision (Bhargavi, Nov 11): option 2, before the merge.** Token fix regardless (done: `fix(pm): one OAuth token fetch…`). Demo start gates on Northside only; Harbor/Lakeside mirrored in a BackgroundTask after the response; per-buyer `paypal_setup` (M9) with Pay/Record disabled and 409 `PAYPAL_NOT_READY` until READY; `POST …/setup` runs or joins the same mirror (≤ 10 s) with a Retry button. M5 changes from "ready together". Gate: local p95 ≤ 7.4 s (baseline 6.4 s + 1 s); Render after merge reported against 2.2–3.4 s. M8 is Bhargavi's browser check.
  - **Lock (deviation from "under the buyer lock", flagged):** the guard is an in-process per-buyer setup task that later callers join, not `pg_advisory_xact_lock(buyer_id)` — CLAUDE.md §6 allows PayPal calls under the buyer lock only for capture, so money writes never wait on PayPal. Backstop across processes: PayPal refuses a second create of the same invoice number (`DUPLICATE_INVOICE_NUMBER`) → search and adopt, so no duplicate PayPal invoices either way.
  - Steps now: **m2b** `feat(pm): Harbor and Lakeside set up after the response (M2, M9)` (backend: start, background, setup endpoints, `paypal_setup`, 409 guards, tests) → local gate → **m3** (order, whoami list) → **m4** (UI incl. setting-up states) → 127.0.0.1:3002 for Bhargavi's M8 → PR.
- **B5 m2b local gate — PASSED.** Option 2 (Northside gates, Harbor/Lakeside in a BackgroundTask) + single token fetch, 5 real starts, same machine/dev DB: server ms 5,114 · 5,048 · 5,066 · 4,726 · 4,494 → **p50 5,048, p95 5,114**; wall p50 5,420, p95 5,838 — under the 6.4 s baseline (limit 7.4 s). Log: 1 OAuth fetch for all 5 starts; 35 creates + 35 sends (5 × 7) — every background setup finished; no warnings.
