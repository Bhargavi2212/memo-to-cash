# Phase P4 — Build plan (how we'll execute `P4.md`)

Spec: `docs/phases/P4.md` v4 (D1–D20, R1–R15, A1–A6, G1–G6, V1–V4, W1–W5, X1–X3, Y1–Y4) — source of truth.
Branch: `p4-agent` from `main` @ `7edbeb1`. FRs: FR-8, FR-9, FR-13 (+ FR-12: the demo's proposal from the live agent). Open check: 4.
**Nothing is cut.** Order (V4 + X2): **0 (+ X1 gate) → 1 → 2 → 3 → 4 → 5 → 5b (approve → PayPal) → 8 → 9 → 6 → 7 → 10**.
State at start: P2 live; all tables exist (`messages`, `proposals`, `llm_usage`) → **no migration**. `agent/llm.py` has only the P0 client + ping. Dev set: `eval/messages.jsonl`, 28 written rows (22 `message`, 6 `ask`; 25 `seed`, 3 `seed_paid_5000`), uncommitted until step 0.

## 1. Who does what, and what blocks what
| Bhargavi | Needed by |
|---|---|
| Approve this plan ("approved") | step 0 |
| ~~G4~~ **resolved (Z3):** ember only, 3 passes, $10 cap; spark once on held-out only if ember fails it | — |
| Real buyer messages, 15–20, redacted, **each labelled by Bhargavi** (label, fields, action) → the **held-out** set (Z5) — I don't read, run or tune on them before the freeze commit | step 7 |
| `LLM_API_KEY` on Render `memo-to-cash-api` (deep health showed it in P0 — confirm) | step 9 |
| PR → merge (no migration) | step 9 |
| If spark wins: edit `LLM_MODEL` in the Render dashboard (`mtc-shared`) by hand (G3) | step 7 |
| Live checks on the site (S-102 question, S-104 chip, repeat, injection), 10 + 10 latency (D16) with me | step 9 |

| Claude Code | Notes |
|---|---|
| Steps 0–8, 10; runs the spike and the full eval locally against the real Meta Model API | never calls the LLM in tests (fake client + recorded fixtures) |

## 2. Steps (one commit each; tests first for validators, `decide()`, dates, caps and scoping; before every commit `ruff check . && ruff format --check .`, `pytest -q`; TS steps also `npx tsc --noEmit && npm run lint && npm test`, `npm run build` on Node 20; push after each step)
| # | Commit | Files | Tests first | Verify |
|---|---|---|---|---|
| 0 | `chore(p4): eval spike` (V1) | `eval/__init__.py`, `eval/messages.jsonl` (the 28 dev rows), `agent/facts.py` (minimal `Facts` + `facts_from_seed(D, paid)` incl. the seeded S-120 pending proposal (W1) and the `payment:1` paid variant via `split_payment` (G2)), `agent/prompts/triage.md` (v0), `agent/actions.py` (minimal `decide()`: D5 cases + W3/W5, dates via a minimal `agent/dates.py`), `eval/baseline.py` (regex: SKU / `$`-amount / weekday + keyword → label, same `decide()`), `eval/run_eval.py` (agent = one `json_schema` call per `message` row; prints per system: label n/N, action n/N, unsafe n, by `job`; `ask` rows shown as "Job A — not in spike") | a tiny `tests/unit/test_eval_spike.py`: `facts_from_seed` totals (842000 / after 5000 → 342000), seeded proposal present, baseline on 3 rows | **run it** (real Meta API, `muse-ember-1.0`, 1 pass) + **p50 / p95 latency per call (X3)** → paste the table here and in PROJECT.md decisions → **X1 gate:** agent not above baseline on dev correct-action rate → 1 h of prompt fixes, then (if still not above) change the claim (D12) and shrink steps 6–7 |
| 1 | `feat(p4): agent facts + tools` | `agent/facts.py` hardened (+ `facts_for_buyer(session, buyer_id, today)`, R1), `agent/tools.py` (`facts_prompt(facts)` with record ids + pure lookups, V3), `agent/schemas.py` (AnswerResult, TriageResult — BACKEND_SCHEMA §5 — Pydantic + JSON schema); importer refactor: `line_status()` out of `_apply`, `_invoice_values` reused (A2) | `tests/unit/test_agent_facts.py` (pure) + `tests/integration/test_agent_facts.py` (`db`): seed facts **equal** DB facts for the same seed; lookups return ids; unknown SKU → none; prompt text has record ids and **no** password / settings / **email** string — only records (R1, Y3); importer tests unchanged + green | `pytest -q` |
| 2 | `feat(p4): validators + decide()` | `agent/validate.py` (D4: citations, money formats incl. `$1,420.5` rejected, day counts, refs, ≤ 600 chars), `agent/actions.py` full D5 (A4 regexes, W3 claimed-approval note, W5, duplicate via pending proposals W1/R3, model `invoice_no` ignored, paid/part-paid `due_cents`), `agent/dates.py` full (W4: `next:FRI`, `next-week:FRI`, `dom:20`, `+3d`, 90-day window, NY, DST) | `test_agent_validate.py`, `test_agent_actions.py` (a table with every D5 edge + one case per dev row's expected action), `test_agent_dates.py` (each weekday, month end, DST) | `pytest -q` |
| 3 | `feat(p4): llm calls + caps + fallbacks` | `agent/llm.py` (`call(job, facts, text, model=None)` → one `json_schema` call, 7.5 s budget as timeout, `max_retries=0`, `max_tokens=1600` (Z2), length / invalid JSON / schema / not configured → typed fallback reason, threadpool + abandon), `agent/caps.py` (D9, NY day, atomic upsert), `agent/prompts/answer.md` + `triage.md` (v1, `version:` header); `ping()` and `list_models()` **unchanged** (Y2) | fake-client tests for **every** D7 path; caps: 20/token, 500 global, 1 unit per request, over cap → fallback (not 429) | `pytest -q` |
| 4 | `feat(p4): ask + message api (FR-8, FR-9)` | `agent/answer.py`, `agent/triage.py`, routes `POST /statement/{token}/ask` + `/message` (D10), proposal creation **under the buyer lock** with the duplicate re-check (D19), G1 fallback merge (one pending fallback REPLY per buyer per day, items appended), Statement `requests` (D17 + A5 mapping, summaries computed at read time), `messages` rows (D18), audit (actor `agent`), no buyer text in logs | `tests/integration/test_agent_api.py` (`db`, fake client): ask → validated answer + `looked_up`; message S-120 on a fresh copy → ADJUSTMENT −2000 INV-1051; injection → expected action only; fallback → 200 + REPLY; G1 merge; `requests` lists seeded + new; two concurrent duplicates → one proposal; 422 `TEXT_EMPTY`/`TEXT_TOO_LONG`; `/message` labelled QUESTION → answer within **one** 7.5 s budget and **1** cap unit (Y4); scoping cases added to `test_scoping.py` | `pytest -q` |
| 5 | `feat(p4): live triage at demo start` | `services/demo.py` (D13: `_create_copy` writes the seed **message only**; `gather(mirror, triage)`; one txn writes the agent's proposal or the P1 one, `evidence.source` agent/seed; mirror failure → nothing) | `test_demo.py`: **update the P1 assertions (A3)** + agent proposal / seed fallback on timeout / mirror failure writes none / exactly one proposal / total ≈ max(mirror, triage) | `pytest -q` |
| 5b | `feat(p4): approve → PayPal invoice update (FR-10)` (X2) | `paypal/invoices.py::put_adjustment` (GET → strip `amount, due_amount, payments, refunds, links, status, gratuity` → add the negative line → PUT `?send_to_recipient=false&send_to_invoicer=false`, Request-Id `<env>-adj-<proposal uid>`), `services/proposals.py` (`decide(proposal_id, APPROVE\|REJECT)`: buyer lock, PENDING only, adjustment `invoice_lines` row + `total_cents` + `ledger_status`, APPROVED → commit → PUT → GET → DONE + `paypal_due_cents`/mismatch, or FAILED + error, retry = same call), admin route `POST /api/supplier/proposals/{id}/decide` | `tests/integration/test_proposals.py` (fake PayPal gains PUT): approve → line + total + PayPal due equal ours; reject; repeat approve → no-op; PayPal failure → FAILED, retry succeeds once; non-ADJUSTMENT kinds → recorded APPROVED with no PayPal call; due < amount → **409 `NEEDS_CREDIT`**, no PayPal call, proposal stays PENDING (Z4) | `pytest -q`; one real sandbox approve on dev |
| 8 | `feat(p4): ask + message ui` | `components/AskBox.tsx` (2 chips, answer card, record chips → scroll + highlight, "Looked up", fallback text), `MessageBox.tsx` (3 demo chips, never S-120; summary + ✓ check chips), `YourRequests.tsx` (D17), invoice anchors `invoice-INV-10xx` (G5), `lib/api.ts` types | tsc/lint/build; local `next start` + api: S-102 question, S-104 chip, repeat → "Already sent", injection, fallback with the key unset; 375 px + desktop | local run |
| 9 | (no commit) live | PR → CI → Bhargavi merges → Render auto-deploys → Start the demo → `evidence.source=agent` → S-102 → S-104 chip → repeat → injection → **10 asks + 10 messages latency (D16)** → **`ms` of 3 live "Start the demo" runs (Y1, D13 keeps ≈ 2–3 s)** → admin approve of the S-104 −$50 proposal → PayPal invoice shows the adjustment (X2) → Render logs: no buyer text, no key | — | P4.md §6 |
| 6 | `feat(p4): eval set, baseline, runner (FR-13)` | `run_eval` full: **ember, 3 passes** (spark only via `--model` for the conditional held-out run, Z3), **three-part scoring (Z1): model correct / system correct / non-REPLY subset with n; fallbacks never a model pass**, token totals + cut-off rate per run, $10 budget guard (stops before exceeding it), Job A scored separately (cited records ⊆ cited, every `expected.facts` string present after money/day normalisation), triage metrics (label, fields, action, unsafe per W3/R4, guard-caught raw errors, fallback, length-cutoff, p50/p95, token counts per model), dev vs held-out reported separately with n (V2), `--model` per call (D20), results files + `--dry` replay; CI runs `--dry` on a small committed fixture | `tests/unit/test_eval_scoring.py` (scoring rules on hand-made outputs) | CI green; `python -m eval.run_eval --dry` |
| 7 | (no commit) eval run + decision | dev runs (ember × 3; prompt fixes allowed → new version each) → **re-run the dev set on the exact prompt versions being frozen** (model correct / system correct / non-REPLY / unsafe / cut-off / latency) and **commit that results file with the freeze** (Z11) → **freeze + commit** baseline + prompt version → **only then** read and run Bhargavi's held-out set **once** (Z5) → spark once on held-out only if ember fails it (Z3; G3 if it wins) → D12 claim (Z6) | — | results committed in step 10 |
| 10 | `docs(p4): results, decisions` | PROJECT.md (eval table, model, latency, status order, file tree), TRD §4/§6/§9, CLAUDE.md §5 (A1, V3, deduction ≤ due at approval), BACKEND_SCHEMA §2/§3/§5/§7/§8, IMPLEMENTATION_PLAN, `eval/results/<date>.{jsonl,md}` | — | `/verify` green |

## 3. Gaps found while planning
| # | Finding | Proposed resolution |
|---|---|---|
| P1 | **Weekday said on that weekday**: "will send payment friday" written on a Friday — today or next week? Also "the 20th" on the 20th. | Today counts (first matching date ≥ today), matching D5's window `today ≤ date ≤ today+90`. Tests pin both. |
| P2 | **How code knows a claimed approval (w27)**: TriageResult has no field for it. | Same pattern as A4: a regex on the raw text (`approved`, `already agreed`, `as discussed`, `you said`) adds the evidence note `claims prior approval — not verified`; it never changes the action. |
| P3 | **w26 (forged model output at exactly the price)**: if the model is fooled into DEDUCTION S-111 $1,600, every D5 guard passes (kept, ≤ price) → an unsafe ADJUSTMENT. The spec relies on the model's label alone. | Add a deterministic **markup guard** in `decide()`: buyer text containing forged-output markers (`</buyer_message>`, a line starting `assistant:` / `system:`, `label=`, `amount_cents=`) → REPLY with evidence "message contains instruction-like markup". It must **not** trigger on plain "ignore previous instructions" (w27 expects a pending proposal). Tested on w16/w17/w26/w27. |
| P4 | Spec says 30 rows; the dev set has 28. | D11 now reads "28 written dev rows + the real held-out set" (folded). |
| P5 | Job A scoring isn't defined precisely (formats of `$8,420.00`, "25 days"). | An answer passes if every `expected.cited_records` id is cited **and** every `expected.facts` string appears after normalising money (`$8,420`, `$8,420.00`, `8,420.00`) and day counts ("25 days", "in 25 days", "25d"); tested in step 6. |
| P6 | `agent/dates.py` is listed under step 3, but `decide()` (step 2) needs it. | Built in step 2. |
| P7 | The step 0 spike needs a triage prompt and `decide()` before steps 1–2 — risk of throwaway code. | Spike files live where steps 1–2 harden them (`agent/facts.py`, `agent/actions.py`, `agent/dates.py`, `agent/prompts/triage.md`); steps 1–2 extend, not rewrite. |
| P8 | Step 0 runs only the triage job; `ask` rows have no Job A prompt yet. | Spike table shows triage for 22 `message` rows; `ask` rows listed as "Job A — from step 3". |
| P9 | ~~G4~~ | **Resolved (Z3):** ember only, 3 passes, $10 cap, token totals per run; spark conditional. |
| P10 | **Time** (§5) is over the 9 h box. | Nothing cut; P3a starts later. |
| **P11 ✅ option 1 (Z4)** | **X2 approve when the invoice's due < the deduction** (paid / part paid — the seeded −$20 after a full payment, P2 D20). The spec gives this "record $X as credit" case to P3a, but X2 now approves in P4. | Proposed: P4's approve applies the PayPal PUT only when due ≥ amount; otherwise **409 `NEEDS_CREDIT`** ("INV-1051 is paid — recording a $20.00 credit comes with the supplier screens") and the proposal stays PENDING for P3a. Alternative: build the credit path now (+~1 h). Your call. |
| P12 | X2 needs an approver in P4, but the supplier screens are P3a. | Admin-only API (`ADMIN_TOKEN`), driven by curl/PowerShell in step 9; P3a puts the button on it. Demo cookie access to `decide` comes with P3a (BACKEND_SCHEMA §8 auth table already allows admin **or** demo cookie). |

## 4. Open checks in this phase
| Check | When | How |
|---|---|---|
| 4 — LLM latency on real statements (< 8 s) | step 9 (Render, 10 + 10) and step 7 (eval p50/p95 from Bhargavi's machine) | one inlined-Facts call per job; > 8 s → shorter Facts text, then the faster model |

## 5. Time estimate
| Step | Est. |
|---|---|
| 0 spike (code + run + table + latency) + X1 gate (≤ 1 h if needed) | 1.5 h (+1 h) |
| 1 facts + tools + schemas + importer refactor | 1.0 h |
| 2 validators + decide + dates | 1.5 h |
| 3 llm + caps + fallbacks + prompts | 1.0 h |
| 4 ask + message api + requests + lock + G1 | 1.5 h |
| 5 demo-start triage + test updates | 0.7 h |
| 5b approve → PayPal invoice update | 1.5 h |
| 8 UI (AskBox, MessageBox, YourRequests, chips) | 1.8 h |
| 9 live + latency | 0.7 h |
| 6 full eval runner + scoring + `--dry` | 1.2 h |
| 7 eval runs + decision (mostly waiting on the API) | 1.0 h |
| 10 docs | 0.5 h |
| **Total** | **~13.9 h** (+1 h if the X1 gate triggers) |

> ⚠️ **Over P4's ~9 h box by ~4.9 h** (5b added by X2). Nothing is cut (your rule); P3a starts later. The biggest time is the UI (step 8) and the eval runs (step 7).

## 6. Build log
| # | What happened | Decision |
|---|---|---|
| B0 | Plan approved with X1–X3 (gate after step 0, approve → PayPal as step 5b, latency in the spike) and Y1–Y4; G4 (eval cost) still the template text → full eval waits. | Written into P4.md §2c and this plan. |
| B1 | Step 0 pulled the importer's `line_status()` refactor (A2, planned for step 1) forward so Facts share the real KEPT/RETURNED/OUT rule; importer tests unchanged (34 pass). | Step 1 keeps the rest (`facts_for_buyer`, equality test). |
| B2 | **Step 0 spike** (22 triage rows, ember, 1 pass): agent 22/22 actions vs baseline 21/22, labels 20/22 each, unsafe 0/0, p50 1.2 s / p95 2.7 s, 1 length cut-off (w19). **X1 gate passed** (by one row, w02) — no prompt-fix hour. | Continue with step 1. Watch the length cut-off rate in the full eval (> 5 % → `max_tokens` 1600). |
| B3 | Bhargavi's review of the spike: it's a **tie**, not a win — w17 (mislabel) and w19 (cut-off) only passed because their fallback REPLY was the expected action. Re-scored (Z1): model correct 20/22 vs baseline 20/22; system 22/22 vs 21/22; non-REPLY 9/9 vs 8/9. | Z1–Z6 into P4.md §2c / D8 / D11 / D12 / D20 / §8 and this plan; results file re-printed. |
| B4 | Bhargavi: two spec changes before step 2 — Z7 multi-item return claims (`skus`, `|`-joined actions), Z8 proposals citable (`proposal:<id>`, `proposal:seed`); 2 new dev rows (w29, w30). | Built in step 1 (schemas, Facts ids, prompt, baseline) and step 2 (decide per SKU). |
| B5 | Bhargavi on step 1: Z7 changed to partial handling (`REPLY:UNKNOWN:<sku>` per unknown item, plain REPLY only if none match; deductions stay all-or-nothing) + dev row w31; Z9 FR tags are FR-8/FR-9 for steps 1–2; Z10 held-out reported as main (n=16) + "pattern also in dev" (n=2). | Runner gets `--group`; step 2 builds Z7. |
| B6 | Bhargavi on step 2: the held-out "pattern also in dev" group is **h17, h18** (`--group` example ids were placeholders; the ids come in the step 7 handover). Z11: before the freeze, re-run the dev set on the current prompt version (v1 changed item reading; spike was v0) and commit the results with the freeze. | Step 7 order updated. |
| B7 | Step 3: the facts text now carries code-computed day counts ("38 days ago", "in 11 days", "18 days overdue") so the model copies them instead of doing date arithmetic — the same values the D4 validator allows. `run_eval` calls the production `llm.call`. | Changes the triage prompt's facts → covered by the Z11 dev re-run. |
| B8 | Step 5: "agent proposal if triage validated" (D13) = the triage gives exactly `ADJUSTMENT:INV-1051:-2000` for the known seed text; anything else (fallback, misread amount, other label, LLM cap) → the P1 seed proposal, logged. Demo start takes one **global** cap unit (D9). `tests/demo_copy.py` builds a full copy without PayPal for other tests. | Never a wrong number in the demo story. |
| B9 | Bhargavi after step 5: **step 9** records the demo-start fallback rate across the live runs — how many "Start the demo" proposals have `evidence.source = "agent"` vs `"seed"`, with n — into PROJECT.md and the README. **Step 7** reports the cut-off rate across the 3 passes; over 5 % → raise `max_tokens` (e.g. 2000) as a **new, reported run** (never a silent change), with triage latency p95 next to it. The held-out file (incl. extra Job A questions) comes at step 7 after the freeze commit. | Steps 7, 9 |
