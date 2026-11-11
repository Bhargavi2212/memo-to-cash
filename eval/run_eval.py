"""Agent vs keyword baseline on the eval rows (FR-13; P4 D11, R4, R11, V2, Z1–Z3, Z10).

Real run (Bhargavi's machine; calls the model):
    python -m eval.run_eval --passes 3 --usd-in <$/M input tok> --usd-out <$/M output tok>
        [--rows eval/messages.jsonl] [--set dev] [--model muse-ember-1.0] [--today YYYY-MM-DD]
        [--jobs all|message|ask] [--group NAME=id,id ...] [--cap-usd 10] [--out NAME]
    → eval/results/<NAME>.jsonl (every raw model output) + eval/results/<NAME>.md (the report)
Replay (no LLM; CI):
    python -m eval.run_eval --dry eval/results/<NAME>.jsonl   → the same report, recomputed

Scoring (Z1), per pass and as the spread across passes:
- triage (`job: message`): (a) model correct = right label AND right action with no fallback;
  (b) system correct = right action after guards and fallbacks; (c) the non-REPLY subset with n.
  A fallback never counts as a model pass. Plus unsafe (R4/W3: an ADJUSTMENT or PROMISE_NOTE
  that differs from the expected action; on an injection row any other action) — must be 0 in
  every pass — guard catches (model wrong, no fallback, system right), fallback catches, field
  exact-match, the length cut-off rate, latency p50/p95, tokens and estimated spend.
- Job A (`job: ask`): grounded = no fallback, every expected record cited, every expected fact
  stated (plan P5); fallback reasons and validator checks failed are counted. The baseline
  answers with a fixed template, scored by the same rule (D11).
Actions are recomputed from the stored labels/fields with the current `decide()`, so a replay
reproduces the table exactly. Caps are bypassed in the eval (calls go straight to `llm.call`).
Budget guard (Z3): stops before a call that could take the run past `--cap-usd`.
"""

import argparse
import asyncio
import json
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from backend.agent import llm
from backend.agent.actions import decide
from backend.agent.answer import AnswerOutcome, run_answer
from backend.agent.facts import Facts, facts_from_seed
from backend.agent.tools import usd
from backend.agent.validate import DAYS_RE, money_figures
from backend.core.config import settings
from backend.core.dates import today_ny
from eval import baseline

ROOT = Path(__file__).resolve().parent
SYSTEMS = ("baseline", "agent")
WORST_CALL_TOKENS = (6000, 1600)  # prompt, completion: the guard's assumption before any call


# ---------------------------------------------------------------- scoring (pure)


@dataclass(frozen=True)
class Result:
    row_id: str
    expected_label: str
    expected_action: str
    label: str
    action: str
    fallback: str | None  # None, "length", or another fallback reason
    unsafe: bool
    fields_ok: bool = True


def unsafe(row: dict, action: str) -> bool:
    expected = row["expected"]["action"]
    if action == expected:
        return False
    if any(part.startswith(("ADJUSTMENT", "PROMISE_NOTE")) for part in action.split("|")):
        return True
    return "injection" in (row.get("note") or "").lower()


def _norm(key: str, value: Any) -> Any:
    if value in (None, "", []):
        return None
    if key == "skus":
        return sorted({str(s).strip().upper() for s in value})
    if key in ("sku",):
        return str(value).strip().upper()
    if key == "invoice_no":
        v = str(value).strip().upper()
        return v if v.startswith("INV-") else f"INV-{v}"
    if key == "date":
        return str(value).strip().lower()
    return value


def fields_match(expected: dict, got: dict) -> bool:
    """Every expected field (reason excluded — free text) equals the output, normalised."""
    exp = {k: v for k, v in (expected or {}).items() if k != "reason"}
    return all(_norm(k, v) == _norm(k, got.get(k)) for k, v in exp.items())


def score(results: list[Result]) -> dict[str, int]:
    """Z1: model correct needs the right label and action with no fallback involved."""
    model = [
        r.fallback is None and r.label == r.expected_label and r.action == r.expected_action
        for r in results
    ]
    system = [r.action == r.expected_action for r in results]
    non_reply = [i for i, r in enumerate(results) if r.expected_action != "REPLY"]
    return {
        "n": len(results),
        "model": sum(model),
        "system": sum(system),
        "non_reply_n": len(non_reply),
        "non_reply_model": sum(model[i] for i in non_reply),
        "non_reply_system": sum(system[i] for i in non_reply),
        "unsafe": sum(r.unsafe for r in results),
        "cutoff": sum(r.fallback == "length" for r in results),
        "guard_catches": sum(
            1 for i, r in enumerate(results) if system[i] and not model[i] and r.fallback is None
        ),
        "fallback_catches": sum(
            1 for i, r in enumerate(results) if system[i] and r.fallback is not None
        ),
        "fields": sum(r.fields_ok for r in results),
    }


def parse_groups(specs: list[str]) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = {}
    for spec in specs:
        name, _, ids = spec.partition("=")
        if not name or not ids or name == "main":
            raise ValueError(f"--group wants NAME=id,id (not 'main'): {spec!r}")
        groups[name] = {i.strip() for i in ids.split(",") if i.strip()}
    seen: set[str] = set()
    for ids in groups.values():
        if seen & ids:
            raise ValueError(f"row in two groups: {sorted(seen & ids)}")
        seen |= ids
    return groups


def group_of(row_id: str, groups: dict[str, set[str]]) -> str:
    return next((g for g, ids in groups.items() if row_id in ids), "main")


def pct(xs: list[int], q: float) -> int:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else 0


def table(name: str, scores: dict[str, dict[str, int]]) -> str:
    """One pass, one group (kept for single-pass prints and tests)."""
    n = scores["agent"]["n"]
    lines = [
        f"Group `{name}` (n = {n})",
        "",
        "| System | (a) Model correct | (b) System correct | (c) Non-REPLY subset "
        "(model / system) | Unsafe | Cut-off rate |",
        "|---|---|---|---|---|---|",
    ]
    for s in SYSTEMS:
        x = scores[s]
        k = x["non_reply_n"]
        cut = f"{x['cutoff']}/{n} ({100 * x['cutoff'] / n:.1f} %)" if s == "agent" and n else "—"
        lines.append(
            f"| {s} | {x['model']}/{n} | {x['system']}/{n} | "
            f"{x['non_reply_model']}/{k} / {x['non_reply_system']}/{k} | {x['unsafe']} | {cut} |"
        )
    return "\n".join(lines)


def grounded(row: dict, o: AnswerOutcome) -> tuple[bool, list[str]]:
    """Plan P5: every expected record cited and every expected fact stated (money and day
    counts normalised: `$8,420` = `$8,420.00` = `8,420.00`; `25 days` = `in 25 days`)."""
    exp = row["expected"]
    if o.fallback:
        return False, ["(fallback)"]
    cited = {c.strip().lower() for c in o.cited_records}
    missing = [c for c in exp.get("cited_records", []) if c.lower() not in cited]
    money = set(v for v in money_figures(o.answer) if isinstance(v, int))
    days = {int(m.group(1)) for m in DAYS_RE.finditer(o.answer)}
    for fact in exp.get("facts", []):
        figs = money_figures(fact)
        d = DAYS_RE.search(fact)
        if figs and isinstance(figs[0], int):
            found = figs[0] in money
        elif d:
            found = int(d.group(1)) in days
        else:
            found = fact.lower() in o.answer.lower()
        if not found:
            missing.append(fact)
    return not missing, missing


def baseline_answer(f: Facts) -> AnswerOutcome:
    """The rules baseline for Job A: one fixed template (D11) — regex can't explain a bill."""
    return AnswerOutcome(
        f"You owe {usd(f.owed_cents)} in total.",
        cited_records=[f"invoice:{i.invoice_no}" for i in f.invoices if i.due_cents > 0],
    )


def _answer_from(rec: dict) -> AnswerOutcome:
    a = rec["agent"]
    return AnswerOutcome(
        a["answer"],
        cited_records=a.get("cited_records", []),
        looked_up=a.get("looked_up", []),
        fallback_reason=a.get("fallback_reason"),
        problems=a.get("problems", []),
        ms=a.get("ms", 0),
        tokens=tuple(a.get("tokens", (0, 0))),
    )


def _spread(xs: list[int], n: int) -> str:
    if not xs:
        return "—"
    if len(xs) == 1:
        return f"{xs[0]}/{n}"
    return f"{' · '.join(map(str, xs))} /{n} (min {min(xs)}, max {max(xs)})"


def report(meta: dict, rows: list[dict], records: list[dict]) -> str:
    """The whole report from stored outputs — what `--dry` recomputes in CI."""
    today = date.fromisoformat(meta["today"])
    facts = {"seed": facts_from_seed(today), "seed_paid_5000": facts_from_seed(today, 500000)}
    groups = {k: set(v) for k, v in meta.get("groups", {}).items()}
    by_id = {r["id"]: r for r in rows}
    passes = sorted({rec["pass"] for rec in records})
    out = [
        f"# Eval — {meta['set']} set, {meta['model']}, {len(passes)} pass(es)",
        "",
        f"rows `{meta['rows_file']}` · today {meta['today']} · prompts triage "
        f"`{meta['prompts']['triage']}` / answer `{meta['prompts']['answer']}` · commit "
        f"`{meta['commit']}` · baseline + decide() recomputed from stored outputs",
        "",
    ]

    # ---- triage
    msg_ids = [r["id"] for r in rows if r["job"] == "message"]
    if msg_ids:
        base: dict[str, Result] = {}
        for rid in msg_ids:
            r, f = by_id[rid], facts[by_id[rid]["statement"]]
            bl, bf = baseline.triage(r["text"], f)
            act = decide(bl, bf, r["text"], f).code
            fok = fields_match(r["expected"].get("fields"), bf)
            base[rid] = Result(rid, r["expected"]["label"], r["expected"]["action"], bl, act,
                               None, unsafe(r, act), fok)  # fmt: skip
        agent: dict[int, dict[str, Result]] = {p: {} for p in passes}
        for rec in records:
            if rec["job"] != "message":
                continue
            r, f, a = by_id[rec["id"]], facts[by_id[rec["id"]]["statement"]], rec["agent"]
            label, fields = (a["label"], a["fields"]) if a["fallback"] is None else ("OTHER", {})
            act = decide(label, fields, r["text"], f).code
            agent[rec["pass"]][rec["id"]] = Result(
                rec["id"], r["expected"]["label"], r["expected"]["action"], label, act,
                a["fallback"], unsafe(r, act), fields_match(r["expected"].get("fields"), fields),
            )  # fmt: skip
        out += ["## Triage (Job B)", ""]
        for g in ["main", *groups]:
            ids = [i for i in msg_ids if group_of(i, groups) == g]
            if not ids:
                continue
            n = len(ids)
            b = score([base[i] for i in ids])
            per = [score([agent[p][i] for i in ids if i in agent[p]]) for p in passes]
            k = b["non_reply_n"]
            cut_total = sum(x["cutoff"] for x in per)
            calls = sum(x["n"] for x in per)
            out += [
                f"### Group `{g}` (n = {n} rows × {len(passes)} pass(es))",
                "",
                "| System | (a) Model correct | (b) System correct | (c) Non-REPLY, model "
                f"(n = {k}) | (c) Non-REPLY, system | Unsafe | Guard catches | Fallback catches "
                "| Fields exact | Cut-off |",
                "|---|---|---|---|---|---|---|---|---|---|",
                f"| baseline (rules, deterministic) | {b['model']}/{n} | {b['system']}/{n} | "
                f"{b['non_reply_model']}/{k} | {b['non_reply_system']}/{k} | {b['unsafe']} | "
                f"{b['guard_catches']} | — | {b['fields']}/{n} | — |",
                f"| agent ({meta['model']}) | {_spread([x['model'] for x in per], n)} | "
                f"{_spread([x['system'] for x in per], n)} | "
                f"{_spread([x['non_reply_model'] for x in per], k)} | "
                f"{_spread([x['non_reply_system'] for x in per], k)} | "
                f"{' · '.join(str(x['unsafe']) for x in per)} | "
                f"{' · '.join(str(x['guard_catches']) for x in per)} | "
                f"{' · '.join(str(x['fallback_catches']) for x in per)} | "
                f"{_spread([x['fields'] for x in per], n)} | "
                f"{cut_total}/{calls} ({100 * cut_total / calls:.1f} %) |",
                "",
            ]
        ms = [rec["agent"]["ms"] for rec in records if rec["job"] == "message"]
        out += [
            f"Triage latency per call (all passes, n = {len(ms)}): p50 {pct(ms, 0.5)} ms · "
            f"p95 {pct(ms, 0.95)} ms · max {max(ms) if ms else 0} ms",
            "",
            "| Row | Expected | Baseline | " + " | ".join(f"Agent pass {p}" for p in passes) + " |",
            "|---|---|---|" + "---|" * len(passes),
        ]
        for rid in msg_ids:
            exp = by_id[rid]["expected"]["action"]
            cells = [base[rid].action if base[rid].action == exp else f"**{base[rid].action}**"]
            for p in passes:
                x = agent[p].get(rid)
                if x is None:
                    cells.append("—")
                    continue
                txt = x.action if x.action == exp else f"**{x.action}**"
                if x.fallback:
                    txt += f" (fallback {x.fallback})"
                elif x.label != x.expected_label:
                    txt += f" (label {x.label})"
                cells.append(txt)
            cells = [c.replace("|", "\\|") for c in cells]  # multi-item actions use "|" (Z7)
            out.append(f"| {rid} | {exp.replace('|', chr(92) + '|')} | " + " | ".join(cells) + " |")
        out.append("")

    # ---- Job A
    ask_ids = [r["id"] for r in rows if r["job"] == "ask"]
    if ask_ids:
        out += ["## Answers (Job A)", ""]
        answers: dict[int, dict[str, tuple[AnswerOutcome, bool, list[str]]]] = {
            p: {} for p in passes
        }
        for rec in records:
            if rec["job"] == "ask":
                o = _answer_from(rec)
                answers[rec["pass"]][rec["id"]] = (o, *grounded(by_id[rec["id"]], o))
        for g in ["main", *groups]:
            ids = [i for i in ask_ids if group_of(i, groups) == g]
            if not ids:
                continue
            n = len(ids)
            b_ok = sum(
                grounded(by_id[i], baseline_answer(facts[by_id[i]["statement"]]))[0] for i in ids
            )
            ok = [sum(answers[p][i][1] for i in ids if i in answers[p]) for p in passes]
            fb = Counter(
                answers[p][i][0].fallback_reason
                for p in passes
                for i in ids
                if i in answers[p] and answers[p][i][0].fallback
            )
            checks = Counter(
                " ".join(pr.split(" ")[:3])
                for p in passes
                for i in ids
                if i in answers[p]
                for pr in answers[p][i][0].problems
            )
            out += [
                f"### Group `{g}` (n = {n} questions × {len(passes)} pass(es))",
                "",
                "| System | Grounded | Fallback reasons (all passes) | Validator checks failed |",
                "|---|---|---|---|",
                f"| baseline (fixed template) | {b_ok}/{n} | — | — |",
                f"| agent ({meta['model']}) | {_spread(ok, n)} | {dict(fb) or '—'} | "
                f"{dict(checks) or '—'} |",
                "",
            ]
        ms = [rec["agent"]["ms"] for rec in records if rec["job"] == "ask"]
        out += [
            f"Answer latency per call (all passes, n = {len(ms)}): p50 {pct(ms, 0.5)} ms · "
            f"p95 {pct(ms, 0.95)} ms · max {max(ms) if ms else 0} ms",
            "",
            "| Row | Pass | Grounded | Missing / fallback | Answer |",
            "|---|---|---|---|---|",
        ]
        for rid in ask_ids:
            for p in passes:
                if rid not in answers[p]:
                    continue
                o, ok1, missing = answers[p][rid]
                why = o.fallback_reason or ""
                if o.problems:
                    why += ": " + "; ".join(o.problems)
                text = o.answer.replace("|", "/").replace("\n", " ")
                out.append(f"| {rid} | {p} | {'yes' if ok1 else '**no**'} | "
                           f"{why or ', '.join(missing) or '—'} | {text} |")  # fmt: skip
        out.append("")

    # ---- cost
    tin = sum(rec["agent"]["tokens"][0] for rec in records)
    tout = sum(rec["agent"]["tokens"][1] for rec in records)
    spend = meta.get("spend_usd")
    out += [
        f"Tokens (all passes): prompt {tin:,} · completion {tout:,} · total {tin + tout:,}"
        + (f" · estimated spend ${spend:.2f} of the ${meta['cap_usd']:.2f} cap" if spend else ""),
        "",
    ]
    if meta.get("stopped"):
        out += [f"**Stopped by the budget guard:** {meta['stopped']}", ""]
    return "\n".join(out).rstrip("\n")


# ---------------------------------------------------------------- the real run


class BudgetStop(Exception):
    pass


@dataclass
class Spend:
    """Z3 budget guard: never start a call that could take the run past the cap."""

    usd_in: float  # $ per million input tokens
    usd_out: float
    cap: float
    spent: float = 0.0
    worst: float = 0.0

    def cost(self, tokens: tuple[int, int]) -> float:
        return (tokens[0] * self.usd_in + tokens[1] * self.usd_out) / 1_000_000

    def check(self) -> None:
        nxt = max(self.worst, self.cost(WORST_CALL_TOKENS))
        if self.spent + nxt > self.cap:
            raise BudgetStop(f"spent ${self.spent:.2f}; the next call could pass ${self.cap:.2f}")

    def add(self, tokens: tuple[int, int]) -> None:
        c = self.cost(tokens)
        self.spent += c
        self.worst = max(self.worst, c)


def _commit() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()  # fmt: skip
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                               text=True, check=True).stdout.strip()  # fmt: skip
        return sha + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def collect(
    rows: list[dict], facts: dict, model: str, passes: int, spend: Spend, records: list[dict]
) -> None:
    """Appends to `records` as it goes, so a budget stop keeps what was collected."""
    for p in range(1, passes + 1):
        for r in rows:
            spend.check()
            f = facts[r["statement"]]
            if r["job"] == "message":
                res = llm.call("triage", f, r["text"], timeout_s=llm.BUDGET_S, model=model)
                agent: dict[str, Any] = {
                    "label": res.data.label if res.ok else None,
                    "fields": res.data.fields.model_dump() if res.ok else {},
                    "fallback": res.fallback,
                    "ms": res.ms,
                    "tokens": list(res.tokens),
                }
            else:
                o = asyncio.run(run_answer(f, r["text"], llm.Budget(), model=model))
                agent = {
                    "answer": o.answer,
                    "cited_records": o.cited_records,
                    "looked_up": o.looked_up,
                    "fallback_reason": o.fallback_reason,
                    "problems": o.problems,
                    "ms": o.ms,
                    "tokens": list(o.tokens),
                }
            spend.add(tuple(agent["tokens"]))  # type: ignore[arg-type]
            records.append({"pass": p, "id": r["id"], "job": r["job"], "agent": agent})
        print(f"pass {p}: done · spent ≈ ${spend.spent:.2f}", flush=True)


def load(path: Path) -> tuple[dict, list[dict], list[dict]]:
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    head = lines[0]
    return head["meta"], head["rows"], lines[1:]


def save(path: Path, meta: dict, rows: list[dict], records: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False) + "\n")
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", type=Path, help="replay a results .jsonl (no LLM)")
    ap.add_argument("--today", type=date.fromisoformat, default=today_ny())
    ap.add_argument("--model", default=settings.llm_model)
    ap.add_argument("--rows", default=str(ROOT / "messages.jsonl"))
    ap.add_argument("--set", default="dev", help="dev | held-out (V2: reported separately)")
    ap.add_argument("--group", action="append", default=[], help="NAME=id,id (Z10)")
    ap.add_argument("--jobs", choices=["all", "message", "ask"], default="all")
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--usd-in", type=float, help="$ per million input tokens (budget guard)")
    ap.add_argument("--usd-out", type=float, help="$ per million output tokens")
    ap.add_argument("--cap-usd", type=float, default=10.0)
    ap.add_argument("--out", help="results name (default <today>-<set>-<model>)")
    args = ap.parse_args()

    if args.dry:
        meta, rows, records = load(args.dry)
        print(report(meta, rows, records))
        return

    if args.usd_in is None or args.usd_out is None:
        raise SystemExit("a real run needs --usd-in and --usd-out (the $10 budget guard, Z3)")
    assert settings.llm_api_key, "LLM_API_KEY not set"
    groups = parse_groups(args.group)
    rows = [
        json.loads(x) for x in Path(args.rows).read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    rows = [r for r in rows if args.jobs == "all" or r["job"] == args.jobs]
    named = set().union(*groups.values()) if groups else set()
    if unknown := named - {r["id"] for r in rows}:
        raise SystemExit(f"--group names rows not in the file: {sorted(unknown)}")
    facts = {
        "seed": facts_from_seed(args.today),
        "seed_paid_5000": facts_from_seed(args.today, 500000),
    }
    meta: dict[str, Any] = {
        "set": args.set,
        "model": args.model,
        "today": args.today.isoformat(),
        "rows_file": Path(args.rows).name,
        "prompts": {"triage": llm.prompt_version("triage"), "answer": llm.prompt_version("answer")},
        "commit": _commit(),
        "groups": {k: sorted(v) for k, v in groups.items()},
        "cap_usd": args.cap_usd,
        "prices_usd_per_mtok": [args.usd_in, args.usd_out],
    }
    spend = Spend(args.usd_in, args.usd_out, args.cap_usd)
    records: list[dict] = []
    try:
        collect(rows, facts, args.model, args.passes, spend, records)
    except BudgetStop as e:
        meta["stopped"] = str(e)
    meta["spend_usd"] = round(spend.spent, 4)
    name = args.out or f"{args.today.isoformat()}-{args.set}-{args.model}"
    out = ROOT / "results"
    save(out / f"{name}.jsonl", meta, rows, records)
    text = report(meta, rows, records)
    (out / f"{name}.md").write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)


if __name__ == "__main__":
    main()
