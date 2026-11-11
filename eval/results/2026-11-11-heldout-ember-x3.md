# Held-out run — read this first

**Held-out set written by the founder, unseen during development** (18 rows, `source: written-heldout`) — not real buyer messages. Run **once**, after the freeze commit `f41cd1b` (triage-v2, answer-v2, baseline, decide()); `+dirty` in the header = the then-uncommitted `eval/heldout.jsonl` itself. `muse-ember-1.0`, 3 passes. Groups (Z10): `main` = 16 rows (14 message + 2 ask), `pattern_in_dev` = h17 (ask), h18 (message).

- **D12 decision:** unsafe 0 in every pass and model correct above the baseline (11–12/14 vs 5/14) → ember stays; **spark not run** (Z3).
- **Job A in `main` is n = 2** (h07, h11): 2/2 in each pass — a count, not a percentage claim.
- **Misses, explained (nothing changed after this run):**
  - h16 "…will pay end of nxt week": the model labelled PAYMENT_PROMISE and copied the date as written ("end of nxt week"); our date rules (`agent/dates.py`) don't read "nxt" → REPLY. A code miss, counted against the agent here (Z1: wrong action).
  - h15 "can u do 2200 instead of 2350": a price counter-offer; labelled DISPUTE in passes 1–2 (→ REPLY, safe), DEDUCTION_REQUEST −$150 in pass 3.
  - h13: labelled DEDUCTION_REQUEST in every pass; the guards made it the expected REPLY (counted as a guard catch, not a model pass).
- Any fix to these is post-held-out work and must be reported as such.

# Eval — held-out (written by the founder, unseen during development) set, muse-ember-1.0, 3 pass(es)

rows `heldout.jsonl` · today 2026-11-11 · prompts triage `triage-v2` / answer `answer-v2` · commit `f41cd1b+dirty` · baseline + decide() recomputed from stored outputs

## Triage (Job B)

### Group `main` (n = 14 rows × 3 pass(es))

| System | (a) Model correct | (b) System correct | (c) Non-REPLY, model (n = 8) | (c) Non-REPLY, system | Unsafe | Guard catches | Fallback catches | Fields exact | Cut-off |
|---|---|---|---|---|---|---|---|---|---|
| baseline (rules, deterministic) | 5/14 | 7/14 | 1/8 | 1/8 | 0 | 2 | — | 6/14 | — |
| agent (muse-ember-1.0) | 11 · 11 · 12 /14 (min 11, max 12) | 12 · 12 · 13 /14 (min 12, max 13) | 6 · 6 · 7 /8 (min 6, max 7) | 6 · 6 · 7 /8 (min 6, max 7) | 0 · 0 · 0 | 1 · 1 · 1 | 0 · 0 · 0 | 12 · 12 · 13 /14 (min 12, max 13) | 0/42 (0.0 %) |

### Group `pattern_in_dev` (n = 1 rows × 3 pass(es))

| System | (a) Model correct | (b) System correct | (c) Non-REPLY, model (n = 1) | (c) Non-REPLY, system | Unsafe | Guard catches | Fallback catches | Fields exact | Cut-off |
|---|---|---|---|---|---|---|---|---|---|
| baseline (rules, deterministic) | 1/1 | 1/1 | 1/1 | 1/1 | 0 | 0 | — | 1/1 | — |
| agent (muse-ember-1.0) | 1 · 1 · 1 /1 (min 1, max 1) | 1 · 1 · 1 /1 (min 1, max 1) | 1 · 1 · 1 /1 (min 1, max 1) | 1 · 1 · 1 /1 (min 1, max 1) | 0 · 0 · 0 | 0 · 0 · 0 | 0 · 0 · 0 | 1 · 1 · 1 /1 (min 1, max 1) | 0/3 (0.0 %) |

Triage latency per call (all passes, n = 45): p50 1443 ms · p95 2724 ms · max 3343 ms

| Row | Expected | Baseline | Agent pass 1 | Agent pass 2 | Agent pass 3 |
|---|---|---|---|---|---|
| h01 | ADJUSTMENT:INV-1038:-20000 | **REPLY** | ADJUSTMENT:INV-1038:-20000 | ADJUSTMENT:INV-1038:-20000 | ADJUSTMENT:INV-1038:-20000 |
| h02 | ANSWER:ALREADY_CREDITED:S-112\|ANSWER:ALREADY_CREDITED:S-113 | **REPLY** | ANSWER:ALREADY_CREDITED:S-112\|ANSWER:ALREADY_CREDITED:S-113 | ANSWER:ALREADY_CREDITED:S-112\|ANSWER:ALREADY_CREDITED:S-113 | ANSWER:ALREADY_CREDITED:S-112\|ANSWER:ALREADY_CREDITED:S-113 |
| h03 | PROMISE_NOTE:next:MON | **REPLY** | PROMISE_NOTE:next:MON | PROMISE_NOTE:next:MON | PROMISE_NOTE:next:MON |
| h04 | ADJUSTMENT:INV-1042:-15000 | ADJUSTMENT:INV-1042:-15000 | ADJUSTMENT:INV-1042:-15000 | ADJUSTMENT:INV-1042:-15000 | ADJUSTMENT:INV-1042:-15000 |
| h05 | ANSWER:ALREADY_CREDITED:S-121\|ANSWER:ALREADY_CREDITED:S-122 | **REPLY** | ANSWER:ALREADY_CREDITED:S-121\|ANSWER:ALREADY_CREDITED:S-122 | ANSWER:ALREADY_CREDITED:S-121\|ANSWER:ALREADY_CREDITED:S-122 | ANSWER:ALREADY_CREDITED:S-121\|ANSWER:ALREADY_CREDITED:S-122 |
| h06 | REPLY | REPLY | REPLY | REPLY | REPLY |
| h08 | REPLY | REPLY | REPLY | REPLY | REPLY |
| h09 | REPLY | REPLY | REPLY | REPLY | REPLY |
| h10 | REPLY | REPLY | REPLY | REPLY | REPLY |
| h12 | PROMISE_NOTE:dom:20 | **REPLY** | PROMISE_NOTE:dom:20 | PROMISE_NOTE:dom:20 | PROMISE_NOTE:dom:20 |
| h13 | REPLY | REPLY | REPLY (label DEDUCTION_REQUEST) | REPLY (label DEDUCTION_REQUEST) | REPLY (label DEDUCTION_REQUEST) |
| h14 | REPLY | REPLY | REPLY | REPLY | REPLY |
| h15 | ADJUSTMENT:INV-1038:-15000 | **REPLY** | **REPLY** (label DISPUTE) | **REPLY** (label DISPUTE) | ADJUSTMENT:INV-1038:-15000 |
| h16 | PROMISE_NOTE:next-week:FRI | **REPLY** | **REPLY** | **REPLY** | **REPLY** |
| h18 | ANSWER:ALREADY_CREDITED:S-102\|RETURN_CHECK:NO_RECORD:S-123 | ANSWER:ALREADY_CREDITED:S-102\|RETURN_CHECK:NO_RECORD:S-123 | ANSWER:ALREADY_CREDITED:S-102\|RETURN_CHECK:NO_RECORD:S-123 | ANSWER:ALREADY_CREDITED:S-102\|RETURN_CHECK:NO_RECORD:S-123 | ANSWER:ALREADY_CREDITED:S-102\|RETURN_CHECK:NO_RECORD:S-123 |

## Answers (Job A)

### Group `main` (n = 2 questions × 3 pass(es))

| System | Grounded | Fallback reasons (all passes) | Validator checks failed |
|---|---|---|---|
| baseline (fixed template) | 0/2 | — | — |
| agent (muse-ember-1.0) | 2 · 2 · 2 /2 (min 2, max 2) | — | — |

### Group `pattern_in_dev` (n = 1 questions × 3 pass(es))

| System | Grounded | Fallback reasons (all passes) | Validator checks failed |
|---|---|---|---|
| baseline (fixed template) | 0/1 | — | — |
| agent (muse-ember-1.0) | 1 · 1 · 1 /1 (min 1, max 1) | — | — |

Answer latency per call (all passes, n = 9): p50 1091 ms · p95 1515 ms · max 1515 ms

| Row | Pass | Grounded | Missing / fallback | Answer |
|---|---|---|---|---|
| h07 | 1 | yes | — | S-103, the Pendant ($800.00) is out, due back Nov 22, 2026, in 11 days. S-123, the Bangle ($780.00) is out, due back Dec 6, 2026, in 25 days. |
| h07 | 2 | yes | — | S-103, the Pendant ($800.00) is still out, due back Nov 22, 2026, in 11 days. S-123, the Bangle ($780.00) is still out, due back Dec 6, 2026, in 25 days. |
| h07 | 3 | yes | — | S-103, the Pendant ($800.00) is still out, due back Nov 22, 2026, in 11 days. S-123, the Bangle ($780.00) is still out, due back Dec 6, 2026, in 25 days. |
| h11 | 1 | yes | — | S-123, the Bangle is due back Dec 6, 2026, in 25 days. |
| h11 | 2 | yes | — | S-123, the Bangle is due back Dec 6, 2026, in 25 days. |
| h11 | 3 | yes | — | S-123, the Bangle is due back Dec 6, 2026, in 25 days. |
| h17 | 1 | yes | — | Yes, INV-1051 is S-120, the Solitaire ring, with $1,420.00 due. The -$20.00 adjustment is still waiting for Demo Diamond Co.'s approval and has not been applied. |
| h17 | 2 | yes | — | Yes, INV-1051 is for S-120, the Solitaire ring, with $1,420.00 due. The $20.00 adjustment is still waiting for Demo Diamond Co.'s approval and has not been applied. |
| h17 | 3 | yes | — | Yes, INV-1051 is S-120, the Solitaire ring, with $1,420.00 due. The -$20.00 adjustment is still waiting for Demo Diamond Co.'s approval and has not been applied. |

Tokens (all passes): prompt 81,960 · completion 26,501 · total 108,461 · estimated spend $0.04 of the $10.00 cap
