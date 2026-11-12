"""Demo-start timing (P3 §M, M2; PM-plan m2/m6): N fresh demo starts against a running api,
p50/p95 of the server's own `ms` and the wall time. Prints timings and status codes only —
never tokens, cookies or ids.

    python -m scripts.demo_timing --base http://127.0.0.1:8001/api --runs 5
    python -m scripts.demo_timing --base https://memo-to-cash-web.onrender.com/api --runs 5
"""

import argparse
import statistics
import time

import httpx


def pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, round(p / 100 * (len(s) - 1)))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()
    server, wall = [], []
    for n in range(1, args.runs + 1):
        with httpx.Client(timeout=60) as c:  # a fresh visitor each time
            t = time.perf_counter()
            r = c.post(f"{args.base}/demo/start", json={})
            w = (time.perf_counter() - t) * 1000
        if r.status_code != 200:
            err = r.json().get("error", {}) if r.headers.get("content-type", "").startswith(
                "application/json") else {}  # fmt: skip
            debug = err.get("paypal_debug_id")
            print(f"run {n}: HTTP {r.status_code} {err.get('code')} debug_id={debug}")
            continue
        ms = r.json()["ms"]
        server.append(ms)
        wall.append(w)
        print(f"run {n}: server {ms} ms, wall {w:.0f} ms")
    if server:
        print(f"server ms: p50 {statistics.median(server):.0f}, p95 {pct(server, 95):.0f}, "
              f"max {max(server)}  (n={len(server)})")  # fmt: skip
        print(f"wall ms:   p50 {statistics.median(wall):.0f}, p95 {pct(wall, 95):.0f}")


if __name__ == "__main__":
    main()
