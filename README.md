# Memo-to-Cash

Buyers get one statement link showing what they kept, returned and owe, and pay many PayPal invoices in one payment; an AI agent explains the bill and turns buyer messages into proposals the supplier approves.

*(The full README comes with P5. This section records the agent's evaluation.)*

## Does the agent work? (P4 eval)

**On 14 held-out messages written by the founder and never seen during development, the agent picked the correct label and action for 11–12 of the 14 in each of 3 passes, vs 5 of 14 for keyword rules (muse-ember-1.0), with 0 unsafe actions; none of its mistakes turned into a wrong money action — each became a message for the supplier to answer.**

- **Looser measure: action only** (right action, label not required, after code checks and fallbacks): agent 12–13/14 per pass, keyword rules 7/14.
- **Answers (Job A):** grounded in the records in 2/2 (n = 2) main held-out questions and 1/1 in the "pattern also in dev" group, in every pass. These are counts, not a percentage claim.
- **How it's built to be safe:** the model only labels and extracts; code (`backend/agent/actions.py`) decides every action from the buyer's own records, and code checks every answer (`backend/agent/validate.py`) — money figures, day counts, record references. The agent never changes money: a supplier approves every proposal. On prompt-injection rows, any action other than the expected one counts as unsafe — 0 in every pass.
- **Method:** dev rows (`eval/messages.jsonl`) were used for tuning; prompts, baseline and decision code were then frozen (commit `f41cd1b`) and the held-out set (`eval/heldout.jsonl`, 18 rows written by the founder) was run once, 3 passes. A miss found by the held-out run ("end of nxt week" — the date code couldn't read "nxt") was fixed afterwards in a separate commit and is **not** counted in these numbers.
- **Reports:** [held-out](eval/results/2026-11-11-heldout-ember-x3.md) · [dev, frozen versions](eval/results/2026-11-11-dev-ember-x3.md). Every raw model output is in the matching `.jsonl`; `python -m eval.run_eval --dry <file>` recomputes a report without calling the model.
- **Live (Render):** questions answered in p50 1.4 s / p95 2.0 s; messages p50 1.2 s / p95 3.0 s; "Start the demo" 2.2–3.4 s, with the agent's own proposal in 4 of 4 starts.
