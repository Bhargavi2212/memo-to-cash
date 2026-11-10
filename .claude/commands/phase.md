Start phase $ARGUMENTS. The spec is `docs/phases/P$ARGUMENTS.md` (source of truth); `docs/IMPLEMENTATION_PLAN.md` is the summary.

**Stage 1 — PLAN (no code, no branch changes except the plan file):**
1. Read the phase spec in full, then every doc section it references (PRD, TRD FR-ids + open checks, APP_FLOW journeys, BACKEND_SCHEMA tables/endpoints/§8 rules, UI_UX screens, CLAUDE.md invariants), and the current code it touches.
2. Write `docs/phases/P$ARGUMENTS-plan.md` in the same format as `docs/phases/P0-plan.md`:
   - §1 who does what and what blocks what (Bhargavi's tasks vs yours);
   - §2 steps, one commit each: commit message, files, tests written first, exact verify command;
   - §3 gaps: every place where the spec is ambiguous, contradicts another doc or the existing code, or can't be done as written — numbered G1, G2… each with your proposed resolution;
   - §4 open checks in this phase and when they run;
   - §5 time estimate per step and total vs the spec's time box.
3. Commit only the plan file (`docs(p$ARGUMENTS): build plan`), print the gap list, and **STOP**. Do not write code.

**Stage 2 — REVIEW:** Bhargavi has the plan reviewed independently. Resolutions are written into the plan (and the spec/docs if they change).

**Stage 3 — APPROVE:** start building only when Bhargavi says "approved" for this plan.

**Stage 4 — BUILD:** follow the plan step by step: tests first for money/split/idempotency logic, then code, then /verify; one small commit per step. If something new comes up that the plan doesn't cover, stop and ask — don't improvise. Keep a build log (B1, B2…) at the end of the plan file.
End with: what was built, what was verified (commands + results), what is left, any doc changes.
