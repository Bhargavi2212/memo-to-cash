# Dev ask rows after written dates in the facts text — 2026-11-10 (not the freeze run)

Dev set, Job A only (`--jobs ask`), `muse-ember-1.0`, 1 pass, answer-v2. The facts text now writes dates as "Oct 3, 2026" (was ISO 2026-10-03); validator unchanged. Grounding 7/7 as before (dev-v2).


Eval — rows=messages.jsonl, today=2026-11-10, model=muse-ember-1.0, prompt=triage-v2
triage rows (job=message): 0   ask rows (Job A, below): 7

Agent latency per call: p50 0 ms · p95 0 ms · max 0 ms · mean 0 ms
Agent tokens: prompt 0 · completion 0 · total 0

| Row | Group | Expected | Baseline | Agent (label) | ms |
|---|---|---|---|---|---|

Job A (answers) — prompt=answer-v2, n = 7

| Grounded (no fallback, expected records cited, expected facts stated) | Fallbacks | Fallback reasons | Validator checks failed |
|---|---|---|---|
| 7/7 (100 %) | 0/7 | — | — |

Answer latency per call: p50 1396 ms · p95 1649 ms · max 1649 ms · tokens prompt 10070 · completion 2823

| Row | Grounded | Missing | Fallback | Cited | Answer | ms |
|---|---|---|---|---|---|---|
| w05 | yes | — | — | invoice:INV-1038, invoice:INV-1042, invoice:INV-1051 | You owe $8,420.00 total. That is $3,500.00 on INV-1038, $3,500.00 on INV-1042, and $1,420.00 on INV-1051. | 1649 |
| w12 | yes | — | — | memo_line:S-120, invoice:INV-1051 | S-120, the Solitaire ring, is due later. It is billed on INV-1051, due Dec 5, 2026, in 25 days, with $1,420.00 due. | 1309 |
| w21 | yes | — | — | memo_line:S-102, return:R-311 | S-102, Oval 1.20ct is not on your bill. It was returned and received on Oct 3, 2026, 38 days ago, under return R-311. | 1363 |
| w22 | yes | — | — | memo_line:S-110, memo_line:S-111, memo_line:S-112, memo_line:S-113, invoice:INV-1042, return:R-318 | For memo M-207, you owe $3,500.00 on INV-1042. S-112, the Emerald 1.50ct, and S-113, the Halo ring, were returned on Oct 20, 2026, 21 days ago, under R-318, and are not on the bill. | 1600 |
| w23 | yes | — | — | memo_line:S-103 | S-103, the Pendant is due back Nov 21, 2026, in 11 days. | 923 |
| w24 | yes | — | — | payment:1, invoice:INV-1042 | Yes, your $5,000.00 payment on Nov 10, 2026 went through, with $1,500.00 applied to INV-1042. The amount due on INV-1042 is $2,000.00, due Nov 20, 2026, in 10 days. | 1396 |
| w30 | yes | — | — | memo_line:S-120, invoice:INV-1051, proposal:seed | No. The -$20.00 adjustment for S-120, the Solitaire ring, on INV-1051 is still waiting for Demo Diamond Co.'s approval. INV-1051 still shows $1,420.00 due. | 1624 |
