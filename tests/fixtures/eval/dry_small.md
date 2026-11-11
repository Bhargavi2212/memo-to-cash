# Eval — fixture set, fixture-model, 2 pass(es)

rows `dry_small.jsonl` · today 2026-11-10 · prompts triage `fixture` / answer `fixture` · commit `fixture` · baseline + decide() recomputed from stored outputs

## Triage (Job B)

### Group `main` (n = 4 rows × 2 pass(es))

| System | (a) Model correct | (b) System correct | (c) Non-REPLY, model (n = 2) | (c) Non-REPLY, system | Unsafe | Guard catches | Fallback catches | Fields exact | Cut-off |
|---|---|---|---|---|---|---|---|---|---|
| baseline (rules, deterministic) | 3/4 | 4/4 | 2/2 | 2/2 | 0 | 1 | — | 4/4 | — |
| agent (fixture-model) | 2 · 3 /4 (min 2, max 3) | 4 · 3 /4 (min 3, max 4) | 2 · 1 /2 (min 1, max 2) | 2 · 1 /2 (min 1, max 2) | 0 · 0 | 1 · 0 | 1 · 0 | 3 · 3 /4 (min 3, max 3) | 1/8 (12.5 %) |

Triage latency per call (all passes, n = 8): p50 1200 ms · p95 4100 ms · max 4100 ms

| Row | Expected | Baseline | Agent pass 1 | Agent pass 2 |
|---|---|---|---|---|
| w01 | ADJUSTMENT:INV-1038:-5000 | ADJUSTMENT:INV-1038:-5000 | ADJUSTMENT:INV-1038:-5000 | ADJUSTMENT:INV-1038:-5000 |
| w17 | REPLY | REPLY | REPLY (label DEDUCTION_REQUEST) | REPLY |
| w19 | REPLY | REPLY | REPLY (fallback length) | REPLY |
| w29 | ANSWER:ALREADY_CREDITED:S-121\|RETURN_CHECK:NO_RECORD:S-123 | ANSWER:ALREADY_CREDITED:S-121\|RETURN_CHECK:NO_RECORD:S-123 | ANSWER:ALREADY_CREDITED:S-121\|RETURN_CHECK:NO_RECORD:S-123 | **ANSWER:ALREADY_CREDITED:S-121** |

## Answers (Job A)

### Group `main` (n = 2 questions × 2 pass(es))

| System | Grounded | Fallback reasons (all passes) | Validator checks failed |
|---|---|---|---|
| baseline (fixed template) | 0/2 | — | — |
| agent (fixture-model) | 2 · 1 /2 (min 1, max 2) | {'validator': 1} | {'money figure 120000': 1} |

Answer latency per call (all passes, n = 4): p50 1100 ms · p95 1100 ms · max 1100 ms

| Row | Pass | Grounded | Missing / fallback | Answer |
|---|---|---|---|---|
| w21 | 1 | yes | — | S-102, the Oval 1.20ct, isn't on your bill. Return R-311 was received Oct 3, 2026, 38 days ago. |
| w21 | 2 | **no** | validator: money figure 120000 not in the records | I'm not sure — I've asked Demo Diamond Co. to reply. |
| w30 | 1 | yes | — | No. The $20.00 request for S-120 is still waiting for Demo Diamond Co.'s approval. |
| w30 | 2 | yes | — | No. The $20.00 request for S-120 is still waiting for approval. |

Tokens (all passes): prompt 18,400 · completion 4,400 · total 22,800 · estimated spend $0.02 of the $10.00 cap
