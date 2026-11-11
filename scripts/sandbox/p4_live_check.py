"""P4 step 9: live check against the deployed app (Render), from any machine.

    python -m scripts.sandbox.p4_live_check --base https://memo-to-cash-web.onrender.com

1. "Start the demo" 3 times → the server-reported ms and the wall time (B11; target ≤ 6 s).
2. On the first two copies: 10 asks + 10 messages (5 + 5 each, under the 20/token cap) →
   p50 / p95 wall ms, fallbacks, labels (D16 open check 4; target answer < 8 s).
Prints buyer ids (for the agent/seed SQL below), never tokens.

Agent vs seed for the demo-start proposals (B9) isn't visible through the public API; run in
the Neon SQL editor on the production branch, with the buyer ids this prints:

    SELECT p.buyer_id, p.evidence->>'source' AS source,
           (m.ai_result->'triage'->>'ms')::int AS triage_ms,
           m.ai_result->'triage'->>'fallback_reason' AS fallback
    FROM proposals p JOIN messages m ON m.id = p.source_message_id
    WHERE m.ai_result->>'seeded' = 'true' AND p.buyer_id IN (<ids>)
    ORDER BY p.buyer_id;
"""

import argparse
import statistics
import time

import httpx

ASKS = [
    "Why is S-102 on my bill?",
    "What do I owe for M-207?",
    "when do i have to send the pendant back",
    "what do i owe total",
    "did the $20 off the solitaire come off yet",
]
MESSAGES = [
    "S-104 has a chipped stone, please take $50 off",
    "I returned S-103 last week",
    "I'll pay the rest on Friday",
    "this statement is wrong. call me",
    "is the bangle due back soon?",
]


def pct(xs: list[int], q: float) -> int:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--starts", type=int, default=3)
    args = ap.parse_args()
    base = args.base.rstrip("/") + "/api"
    tokens, ids = [], []
    with httpx.Client(timeout=60) as c:
        print("| Start | Server ms | Wall ms | Buyer id |\n|---|---|---|---|")
        for n in range(args.starts):
            t0 = time.perf_counter()
            r = c.post(f"{base}/demo/start", json={})
            wall = int((time.perf_counter() - t0) * 1000)
            if r.status_code != 200:
                print(f"| {n + 1} | — | {wall} | HTTP {r.status_code} {r.text[:120]} |")
                continue
            body = r.json()
            tokens.append(body["statement_token"])
            ids.append(body["buyer_id"])
            print(f"| {n + 1} | {body['ms']} | {wall} | {body['buyer_id']} |")

        rows: list[tuple[str, str, int, str, bool]] = []
        for i, token in enumerate(tokens[:2]):
            for q in ASKS:
                t0 = time.perf_counter()
                r = c.post(f"{base}/statement/{token}/ask", json={"question": q})
                ms = int((time.perf_counter() - t0) * 1000)
                d = r.json() if r.status_code == 200 else {}
                rows.append(("ask", q, ms, "QUESTION" if d else f"HTTP {r.status_code}",
                             bool(d.get("fallback", True))))  # fmt: skip
            for m in MESSAGES:
                t0 = time.perf_counter()
                r = c.post(f"{base}/statement/{token}/message", json={"text": m})
                ms = int((time.perf_counter() - t0) * 1000)
                d = r.json() if r.status_code == 200 else {}
                rows.append(("message", m, ms, str(d.get("label") or f"HTTP {r.status_code}"),
                             bool(d.get("fallback", True))))  # fmt: skip
            print(f"copy {i + 1}: done")

    for job in ("ask", "message"):
        ms = [r[2] for r in rows if r[0] == job]
        if ms:
            fb = sum(r[4] for r in rows if r[0] == job)
            mean = int(statistics.mean(ms))
            print(f"\n{job}: n={len(ms)} p50 {pct(ms, 0.5)} ms · p95 {pct(ms, 0.95)} ms · "
                  f"max {max(ms)} ms · mean {mean} ms · fallbacks {fb}")  # fmt: skip
    print("\n| Job | Text | Wall ms | Label | Fallback |\n|---|---|---|---|---|")
    for job, text, ms, label, fb in rows:
        print(f"| {job} | {text} | {ms} | {label} | {'yes' if fb else '—'} |")
    print(f"\nbuyer ids for the agent/seed SQL: {', '.join(map(str, ids))}")


if __name__ == "__main__":
    main()
