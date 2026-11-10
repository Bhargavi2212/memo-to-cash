# Phase P1 — Build plan (how we'll execute `P1.md`)

Spec: `docs/phases/P1.md` v2 (source of truth, D1–D25). This file is the working plan: order, dependencies, checks, and the gaps found while planning.
Branch: `p1-data-mirror` from `main` @ `610da98`. FRs: FR-1, FR-2, FR-12 (backend), FR-15 (link in note). Open checks: 1, 6, 8, 9, 10, 11.
State at start: P0 live on Render; Neon `memo-to-cash` (`delicate-moon-76690980`) with `main`/`dev`/`test` all at 0001; local `.env` has `TEST_DATABASE_URL` (test branch, direct), `DEMO_COOKIE_SECRET`, `IP_HASH_SECRET`, `DEMO_MODE=true`, `APP_BASE_URL`; **missing `SANDBOX_BUYER_EMAIL`** (G2). APIMatic plugin **not visible in this session** (G1).

## 1. Who does what, and what blocks what
| Bhargavi | Needed by |
|---|---|
| **Install/enable the APIMatic Context Plugin for PayPal in Claude Code and restart the session** (no APIMatic tools or plugin are loaded now) + screenshot one lookup → `docs/img/apimatic-1.png` | step 3 check (D23); blocks steps 5–8 rows/commits |
| `SANDBOX_BUYER_EMAIL` (sandbox **Personal** account email) in local `.env` **and** on Render `memo-to-cash-api` | step 7 (real sandbox), step 9 (seed buyer), step 10 |
| Approve push of `610da98` (docs on `main`) | before the P1 PR (the PR diff is cleaner if `main` is pushed first) |
| Production `alembic upgrade head` (0002) before merge | step 10 |
| Open the PR / approve merge, check the 3 invoices in the sandbox **business** account, screenshot → `docs/img/paypal-invoice-1.png` | step 10 |
| Submit the APIMatic claim form | step 11 |

| Claude Code | Notes |
|---|---|
| Steps 1–9, 11; runs migrations on `dev` and (via conftest) `test` | never production (blocked by permission rules + G-rule) |
| Step 7 against the real PayPal sandbox (creates/cancels a few test invoices) | needs G1 + G2 resolved |

Blocks: steps 1–4 can start immediately (no PayPal). Steps 5–8 need the APIMatic plugin (D23). Step 7 needs `SANDBOX_BUYER_EMAIL`. Step 10 needs all of the above + production migration.

## 2. Steps (one commit each; tests written first; `pytest -q` + ruff green before every commit)
Order changed vs spec — see **G3** (run the open checks early); if the APIMatic plugin isn't ready (G1), run steps 5 → 6 → 7 first and come back (G24). Spec step numbers in brackets.
**Every step:** tests first · `/review-money` checklist before any commit touching money or PayPal (steps 1–4, 7–9; G19) · new files added to the PROJECT.md tree in the same commit (G27) · push the branch after each step once Bhargavi OKs pushing (G26).

| # | Commit | Files | Tests first | Verify |
|---|---|---|---|---|
| 1 [3] | `feat(p1): money, dates, settings` | `backend/core/money.py`, `backend/core/dates.py`, `config.py` (+`supplier_name`, `paypal_past_dates_ok`), `render.yaml` (`SUPPLIER_NAME`; fix "Starter" comment), `backend/main.py` logging (D19), `requirements.txt` (+`tzdata`, `python-dotenv`) | `tests/unit/test_money.py` (`to_cents("1150")`, `"1150.5"`, `"0.05"`; rejects `"1.001"`, `"-1"`, `"1e3"`, floats; `fmt_paypal(115000)=="1150.00"`), `test_dates.py` (NY today across DST, `offset`, `age_days`, `days_overdue`=0 on due date, 18 for D-18), `test_config.py` new fields | `pytest -q tests/unit` · ruff · **APIMatic answers in this session (D23) — else stop** |
| 2 [5] | `feat(p1): paypal client [apimatic]` | `backend/paypal/client.py` (D14/D15/D16/D19; `get_paypal()` dependency), `docs/APIMATIC.md` row, `tests/conftest.py` (`anyio_backend` = asyncio, G17) | `tests/unit/test_paypal_client.py` (`MockTransport`, `@pytest.mark.anyio`): token reused until `expires_in-60`, refreshed after; retry on 503/429/timeout, not on 400/422; same `PayPal-Request-Id` on every retry; `PayPalError` fields + `transient` flag; caplog has no token/secret/email | `pytest -q tests/unit/test_paypal_client.py` |
| 3 [6] | `feat(p1): invoice functions [apimatic]` | `backend/paypal/invoices.py` (`build_body`, `create`, `send`, `get`, `search_by_number`, `record_payment`), `tests/fixtures/paypal/*.json` (hand-written from APIMatic schema), `BACKEND_SCHEMA.md §4` corrected (D7 field paths), `docs/APIMATIC.md` rows | `tests/unit/test_invoices.py`: body (dates, `"1150.00"`, DUE_ON_DATE_SPECIFIED, partial payment, note link, memo, bill-to name, supplier name, items description), id from `href`, Request-Id keys `<env>-inv-<uid>` / `<env>-send-<uid>`, record body has no `payment_id`; D22 dates when flag false | `pytest -q tests/unit/test_invoices.py` |
| 4 [7] | `chore(p1): paypal open checks [apimatic]` | `scripts/sandbox/__init__.py`, `scripts/sandbox/p1_open_checks.py` (uses the real body builder; #1 PUT and the final **cancel** of every invoice it created are inline calls — tolerate cancel failures, note whether PayPal emailed on the PUT; G25), `docs/APIMATIC.md` rows for PUT + cancel, real fixtures with emails redacted to `buyer@example.com` / `supplier@example.com` replace hand-written ones, PROJECT.md decisions + TRD §9 results, `render.yaml` `PAYPAL_PAST_DATES_OK=false` only if #8 fails | — (script; fixtures re-run step 3 tests) | `python -m scripts.sandbox.p1_open_checks` → output pasted in PROJECT.md; `pytest -q tests/unit/test_invoices.py` still green on real fixtures |
| 5 [1] | `test(p1): integration test database` | `tests/conftest.py` (D10/D11: `db` marker + fixture, guard, `DROP SCHEMA`, migrate, truncate-after; schema reset is a **lazy session fixture** used only by `db` — G20), `pyproject.toml` (marker), `.github/workflows/ci.yml` (postgres:16 service, D12), `tests/integration/test_db_smoke.py` (`SELECT 1`, stays — G5) | the smoke test itself + unit test of the guard (refuses when the URL equals `.env`'s `DATABASE_URL`/`DATABASE_URL_UNPOOLED`) | `pytest -q` locally (db tests run on Neon `test`); without `TEST_DATABASE_URL` → skipped; with `REQUIRE_TEST_DB=1` and no URL → fail; CI run shows db tests **passed, not skipped** |
| 6 [2] | `feat(p1): core tables` | `backend/db/models.py`, `env.py` (`target_metadata = Base.metadata`), `versions/0002_core_tables.py` (autogenerate → read + fix: enums, partial indexes, CHECKs, `gen_random_uuid()`, downgrade drops tables **and** enum types) | `tests/integration/test_schema.py` (`db`): duplicate `(buyer_id, memo_no)`, `paid_cents > total_cents`, `price_cents <= 0`, duplicate real-buyer email, same email twice for two demo sessions **allowed**, memo line on two invoices — rejected | on `dev`: `alembic upgrade head` → `downgrade -1` → `upgrade head`; `pytest -q tests/integration/test_schema.py` |
| 7 [4] | `feat(p1): csv importer` | `backend/services/audit.py` (`log(session, actor, action, ref, detail)` — G9), `backend/services/importer.py` (`import_csv(session, text, *, demo_session=None) -> ImportResult`; `pg_advisory_xact_lock(buyer_id)` for existing buyers, D20; audit `import.completed`) | `tests/integration/test_importer.py` (`db`): seed CSV → exact counts + statuses (5/5/2, totals 3500/3500/1420, `paypal_invoice_number` matches `^INV-\d{4}-[a-z0-9]{6}$`); same file again → 0/0; **one test per validation rule** in BACKEND_SCHEMA §6 (row number + nothing written); change to a mirrored invoice → row error; demo_session → new buyer each time | `pytest -q tests/integration/test_importer.py` |
| 8 [8] | `feat(p1): mirror service [apimatic]` | `backend/services/mirror.py::mirror_invoices(buyer_id, paypal)`: load rows (threadpool) → `gather` PayPal (async) → write results in one transaction (threadpool) — D13/G21; audit `invoice.mirrored`; `docs/APIMATIC.md` row | `tests/integration/test_mirror.py` (`db`, MockTransport on real fixtures): all OPEN + url/status/due stored; rerun → no PayPal writes; send failed → next run GET + send only; create → `DUPLICATE_INVOICE_NUMBER` → search & adopt; no search on first attempt (D4); 4xx vs 5xx classification; due/date mismatch → `mismatch`; D22 fallback dates → no mismatch; `APP_BASE_URL` missing → refuses | `pytest -q tests/integration/test_mirror.py` |
| 9 [9] | `feat(p1): demo start` | `backend/services/demo.py` (`build_csv(D)`, `start_demo()`, `read_demo_cookie()` helper for P3 — G23; `ip_hash = sha256(ip + IP_HASH_SECRET)`), `backend/routes/demo.py`, `backend/main.py` (router), `seed/__init__.py` + `seed/demo_seed.py` (G10), `requirements.txt` (+`itsdangerous`) | `tests/integration/test_demo.py` (`db`, `TestClient(app, base_url="https://testserver")`): all spec cases (two starts → two buyers/sessions; 16th → 429 `RATE_LIMITED`; 61st global → 429 `DEMO_HOURLY_LIMIT`; failed start → 503/502, no cookie, `demo_ready=false`, rate not incremented; missing settings → 503 `NOT_CONFIGURED` names only; DEMO_MODE off → 404; seed owed 842000, 5/5/2, INV-1038 overdue 18; proposal + message shape per §8 + audit actor `system`; cookie attributes `HttpOnly; Secure; SameSite=Lax; Path=/`, 7-day max-age; `read_demo_cookie` → tampered/expired = None; WARNING logged at 48/60 global; `PAYPAL_REJECTED` logs debug_id at ERROR — G31); unit `test_build_csv` (dates from a chosen D, never hard-coded — G29) | `pytest -q` · `python -m seed.demo_seed` against `dev` + real sandbox (prints token + ms) |
| 10 [10] | (no commit) real run | — | — | Bhargavi: prod migration → PR → CI green → merge. Then `Invoke-RestMethod -Method Post https://memo-to-cash-web.onrender.com/api/demo/start` ×2 → tokens, `ms` ≤ 6000; sandbox business account shows 3 invoices correctly; screenshot |
| 11 [11] | `docs(p1): status, results, apimatic claim` | PROJECT.md (status, tree "Starter" fix), `docs/APIMATIC.md` (incl. **Setup** section: install date, config path), P1.md §6 ticks, this file's build log | — | `/verify` green |

## 3. Gaps and misalignments found while planning
| # | Finding | Severity | Proposed resolution |
|---|---|---|---|
| G1 | **APIMatic plugin is not available in this session**: no APIMatic tools are loaded and `~/.claude/plugins` only has `neon`. D23 forbids writing rows without real lookups. | **Blocker** for steps 2–4, 8 | Bhargavi installs/enables it and restarts Claude Code before step 2 (step 1 confirms). If it can't be installed today, decide: build steps 2–4 from §4 facts + sandbox results and drop the APIMatic prize claim (Render stays). |
| G2 | `SANDBOX_BUYER_EMAIL` is missing in `.env` and on Render (left blank in P0); steps 4 (record payment needs a recipient), 9 (seed buyer email) and 10 need it. Also not in the `NOT_CONFIGURED` list. | Blocker for 4, 9, 10 | Bhargavi adds it before step 4. |
| G3 | Spec says "run early (judged path depends on it)" but orders the open checks as step 7, after 6 steps of DB work. The checks need only money/dates (step 3) + client + invoices — no DB. | Ordering | Run spec steps 3 → 5 → 6 → 7 first, then 1 → 2 → 4 → 8 → 9 (table §2). A failing #8/#10 is then known before the mirror is written. |
| G4 | `/demo/start` deadline 20 s (D6) contradicts TRD §4 Performance "every request answers within 10 s". | Doc conflict | Add to TRD §4: "except `/demo/start` (20 s deadline, P1 D6)". |
| G5 | Step 1 says a deliberately failing `db` test proves CI runs them, "then removed" — costs an extra push + CI round-trip and leaves no lasting proof. | Small | Keep a permanent `test_db_smoke.py` (`SELECT 1` + `alembic_version = head`) and check the CI log shows it **passed** (not skipped). `REQUIRE_TEST_DB=1` already makes a missing DB fail. |
| G6 | D17 "settings check" before work doesn't list which settings `/demo/start` needs. | Spec gap | `NOT_CONFIGURED` if any missing: `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_BUSINESS_EMAIL`, `SANDBOX_BUYER_EMAIL`, `DEMO_COOKIE_SECRET`, `IP_HASH_SECRET`, `APP_BASE_URL`. Written into P1 D16. |
| G7 | D15 retries 3× with per-call timeout 8 s (D6) → one call can take ~24 s + backoff, longer than the 20 s demo deadline; retry also re-sends a create whose first attempt may have succeeded. | Logic | Keep both: the 20 s `asyncio.wait_for` deadline wins (→ 503 `PAYPAL_UNAVAILABLE`); retried creates reuse the same Request-Id (#10) and D4 search-and-adopt covers it if PayPal doesn't honour it. Backoff 0.5 s/1 s/2 s. State in P1 D15. |
| G8 | Importer API isn't defined (how `demo_session` reaches it — the CSV has no such column), nor its return shape. | Spec gap | `import_csv(session, text, *, demo_session: str \| None = None, is_demo=False) -> ImportResult(created: dict[str,int], updated: dict[str,int], errors: list[{row,msg}], buyer_ids)`; caller commits. P3 endpoint maps it to the §2 response. |
| G9 | PROJECT.md tree lists `services/audit.py` (P1) but no spec step creates it; importer, mirror and seed all write audit rows. | Small | Create it in step 7 (importer), one function `log(...)`. |
| G10 | `python -m seed.demo_seed` (CLAUDE.md) needs `seed/__init__.py`; spec doesn't say what the CLI does against which DB. | Small | `seed/__init__.py`; CLI runs `start_demo()` on the DB in `.env` (`dev`) with real sandbox, prints `statement_token`, `buyer_id`, `ms` — no cookie, no rate limit. Refuses when `APP_ENV=prod`. |
| G11 | PROJECT.md tree and `render.yaml` header still say "Starter" (renamed `0.5c-512mb` in P0). | Cosmetic | Fix in step 1 (`render.yaml` is touched there anyway) + tree in step 11. |
| G12 | Statement link in the invoice note (`/s/<token>`) 404s until P2 builds the page — the step 10 screenshot will show a link that doesn't work yet. | Expectation | Accept; re-check the link after P2. Note it in step 10. |
| G13 | Failed demo starts leave orphan buyers (and possibly PayPal sandbox invoices) — unreachable (D9) but never cleaned up. | Small | Accept for v1 (sandbox). Cleanup job = P3 sweep candidate; listed, not built. |
| G14 | Demo start does 3 PayPal calls per invoice (create, send, GET for `recipient_view_url`) = 9 calls + token, with `Semaphore(5)` → ~3 sequential rounds; ≤ 6 s depends on sandbox latency. | Risk | Measure in step 9 (`ms`) and step 10; if > 6 s, the spec's fallback (P3 pre-built pool) applies. Also check whether the send response already holds the payer URL (APIMatic) to skip the GET. |
| G15 | D10 `DROP SCHEMA public CASCADE` on the Neon `test` branch requires owning `public`; Neon's `neondb_owner` owns the database (PG17: `public` owned by `pg_database_owner`), so it should work — unverified. | Check | Verify in step 5; fallback: drop every table + enum type by name. |
| G16 | Request-Id for the open-checks script's record payment (#6/#9) has no allocation row. | Small | Script uses `<env>-alloc-<uuid4>` per call (fresh uuid each run). |
| G17 | `@pytest.mark.anyio` needs the anyio pytest plugin (shipped with `anyio`, already installed via httpx) and an `anyio_backend` fixture returning `"asyncio"` so trio isn't tried. | Small | Add `anyio_backend` fixture in `tests/conftest.py` (step 2). |
| G18 | Spec time box ~7 h; this plan estimates **~8.6 h** (§5). | **Over box** | See §5 cut list. |
| G19 | CLAUDE.md §2 requires the `/review-money` checklist for money/PayPal changes; the spec's steps don't mention it. | Process | Run it before the commits of steps 1–4 and 7–9; answers noted in the commit body when something needed fixing. |
| G20 | A session-wide schema reset would hit Neon (and wipe the `test` branch, ~3–5 s cold start) on every `pytest tests/unit` run too. | Small | Reset + migrate in a lazy session fixture that only `db` tests request. |
| G21 | D13 says DB work goes through `run_in_threadpool`, but `mirror_invoices` is async and must also read/write the DB; doing sync DB calls inside it would block the event loop. | Design | Mirror = load (threadpool) → `gather` (async PayPal only) → write (threadpool, one transaction). |
| G22 | D9 ("all statement/supplier/demo queries require `demo_ready=true`") has no consumer in P1 — the first queries arrive in P2/P3. | Forward | Carry into the P2/P3 specs and `test_scoping.py`; P1 tests only assert the flag's value. |
| G23 | D8's "tampered/expired = absent" has no reader in P1 (supplier routes are P3). | Small | Add `read_demo_cookie()` + unit test in step 9 (cheap) so P3 only wires it in. |
| G24 | Plan order puts the APIMatic-dependent steps first; if G1 isn't resolved, that would stall the phase. | Ordering | Fallback order: 5 → 6 → 7 (DB, importer) while waiting, then 1–4, 8, 9. |
| G25 | Spec step 7 says "finally cancel what it can" — cancel isn't in `invoices.py` and has no APIMatic row; #1's PUT also needs one. | Small | Inline PUT + cancel calls in the script, each with an APIMatic lookup row. |
| G26 | CLAUDE.md §8 says push at the end of every task; in this session pushes wait for Bhargavi's OK (`610da98` on `main` still unpushed). | Process | Bhargavi decides: push after every step, or after steps 4, 7 and 9. Push `main` first so the P1 PR only shows P1. |
| G27 | CLAUDE.md §0: the PROJECT.md tree must change in the same commit as a new file. New here: `tests/integration/test_db_smoke.py`, `seed/__init__.py`, `tests/fixtures/paypal/*.json` (already listed), `scripts/sandbox/__init__.py` (listed). | Small | Update the tree in steps 5 and 9. |
| G28 | Cost (Bhargavi's budget limit): P1 adds no paid service — PayPal sandbox and the Neon free plan only. The P0 budget section (limit + Muse price) is still pending. | Note | Nothing to build; budget section stays a carry-over. |
| G29 | Invariant 8 (no hard-coded calendar dates): seed tests must derive dates from a chosen D; DST unit tests use fixed instants by necessity. | Clarify | Allowed — the invariant covers seed, UI and examples; tests that pin a timezone edge say so in a comment. |
| G30 | D16 `debug_id` at ERROR and D17's 80 % WARNING weren't in the step 9 test list. | Small | Added to step 9 tests (caplog). |

## 4. Open checks in this phase
| Check | When | How | Fallback if it fails |
|---|---|---|---|
| #8 past invoice/due dates | step 4 (early) | create+send+GET with `invoice_date` D-48, due D-18 | `PAYPAL_PAST_DATES_OK=false` (D22) in `.env` + `render.yaml` |
| #10 Request-Id honoured on create | step 4 | same create twice, same Request-Id → same id? | D4 search & adopt (already built in step 8) |
| #11 duplicate number error name | step 4 | create with an existing `invoice_number` | match on whatever name is returned (constant in `invoices.py`) |
| #6 transaction id in record response | step 4 | record $50 | `[MTC:<uid>]` note token (P2) |
| #9 tiny recorded amount | step 4 | record $0.05 | BACKEND_SCHEMA §8 minimum rule (P2) |
| #1 PUT on part-paid | step 4 | PUT −$20 line with the strip rule | P3: adjustments only on unpaid invoices (seed already chosen that way) |
All six results → PROJECT.md decisions log + TRD §9 in the step 4 commit.

## 5. Time estimate
| Step | Est. |
|---|---|
| 1 money, dates, settings | 0.5 h |
| 2 PayPal client | 0.6 h |
| 3 invoice functions | 0.6 h |
| 4 open checks (write + run + fixtures + results) | 0.8 h |
| 5 test DB + CI | 0.6 h |
| 6 tables + migration | 1.0 h |
| 7 importer (14 validation rules) | 1.4 h |
| 8 mirror | 1.0 h |
| 9 demo start + seed CLI | 1.2 h |
| 10 Render run (deploy waits) | 0.4 h |
| 11 docs + claim | 0.3 h |
| `/review-money` passes + fixes (G19) | 0.2 h |
| **Total** | **~8.6 h** |

> ⚠️ **Over P1's ~7 h box by ~1.6 h.** Proposed cuts, in order, if the clock says so (invariants untouched):
> 1. Importer validation tests: cover the 6 rules the seed/demo path can hit (unknown memo/invoice, bad date/price, duplicate key, mirrored-invoice change, line on two invoices, zero-line invoice); remaining rules implemented but tested in P2 morning (spec §7 allows edge tests to trail) — saves ~0.4 h.
> 2. Migration downgrade round-trip verified once by hand, no test — saves ~0.1 h.
> 3. Hand-written fixtures in step 3 kept minimal (create/send/get only) since step 4 replaces them with real ones — saves ~0.2 h.
> 4. Seed CLI (G10) built last / P2 — saves ~0.2 h.
> With 1–4: **~7.7 h** — still ~0.7 h over; the remaining lever is spec §7 ("> 9 h → ship steps 1–9 core"), so no further cut is proposed now. Not cut: open checks, mirror idempotency, demo-start error paths, Request-Id keys.
> Blockers G1/G2 are not in the estimate; waiting on them adds wall-clock time, not build time (steps 5–7 can run meanwhile).

## 6. Build log
_(B1, B2… added during Stage 4)_
