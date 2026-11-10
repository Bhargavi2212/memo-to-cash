"use client";
// Receipt after paying (UI_UX §4.2, P2 D16): amount, date, capture id, per invoice "paid in
// full" / "$X left", View on PayPal (judged moment 3), new balance. While PayPal invoices are
// still being updated it polls every 2 s for up to 20 s, then offers "Retry updating PayPal"
// (R4, R14).
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, api, type Receipt as ReceiptData } from "@/lib/api";
import { shortDate } from "@/lib/dates";
import { formatCents } from "@/lib/money";

import { StatusChip } from "./StatusChip";

const POLL_MS = 2000;
const POLL_FOR_MS = 20000;

type Load =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "error"; message: string }
  | { kind: "ready"; receipt: ReceiptData };

const unsettled = (r: ReceiptData) =>
  r.status === "CAPTURED" && r.allocations.some((a) => !a.recorded && a.record_state !== "FAILED");

export function ReceiptSkeleton() {
  return <div aria-busy="true" className="h-64 animate-pulse rounded-card bg-line" />;
}

export function Receipt() {
  const { token, paymentId } = useParams<{ token: string; paymentId: string }>();
  const id = Number(paymentId);
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [timedOut, setTimedOut] = useState(false); // polling window (20 s) is over
  const [tick, setTick] = useState(0);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .receipt(token, id)
      .then((receipt) => live && setLoad({ kind: "ready", receipt }))
      .catch((e: unknown) => {
        if (!live) return;
        if (e instanceof ApiError && e.status === 404) setLoad({ kind: "missing" });
        else setLoad({ kind: "error", message: e instanceof ApiError ? e.message : "Something went wrong." });
      });
    return () => {
      live = false;
    };
  }, [token, id, tick]);

  useEffect(() => {
    const t = setTimeout(() => setTimedOut(true), POLL_FOR_MS);
    return () => clearTimeout(t);
  }, []);

  // Poll while PayPal invoices are still being updated (≤ 20 s).
  const polling = load.kind === "ready" && unsettled(load.receipt) && !timedOut;
  useEffect(() => {
    if (!polling) return;
    const t = setTimeout(() => setTick((n) => n + 1), POLL_MS);
    return () => clearTimeout(t);
  }, [polling, tick]);

  const back = (
    <Link href={`/s/${token}`} className="inline-flex min-h-11 items-center text-accent underline">
      ← Back to statement
    </Link>
  );

  if (load.kind === "loading") return <ReceiptSkeleton />;
  if (load.kind === "missing")
    return (
      <div className="rounded-card border border-line bg-surface p-6">
        <h1 className="text-xl font-semibold">We couldn&apos;t find this receipt</h1>
        <p className="mt-2 text-muted">It may belong to a payment that didn&apos;t go through.</p>
        <div className="mt-3">{back}</div>
      </div>
    );
  if (load.kind === "error")
    return (
      <div role="alert" className="rounded-card border border-line bg-surface p-6">
        <p>{load.message}</p>
        <button
          type="button"
          onClick={() => setTick((n) => n + 1)}
          className="mt-3 min-h-11 rounded-card bg-accent px-4 font-medium text-white"
        >
          Try again
        </button>
      </div>
    );

  const r = load.receipt;
  const stuck = unsettled(r) && !polling;
  const pending = r.status === "CAPTURE_PENDING";

  return (
    <div className="space-y-6">
      <header className="rounded-card border border-line bg-surface p-5">
        <p className="text-sm font-medium text-success">{pending ? "Payment received" : "Payment complete"}</p>
        <p className="tabular mt-1 text-3xl font-semibold">{formatCents(r.amount_cents)}</p>
        <p className="mt-1 text-sm text-muted">
          {r.received_at ? shortDate(r.received_at) : "Today"}
          {r.paypal_capture_id && <> · PayPal capture {r.paypal_capture_id}</>}
        </p>
      </header>

      {pending && (
        <p role="status" className="rounded-card border border-line bg-surface px-4 py-3 text-sm">
          PayPal is still processing your payment. We&apos;ll apply it to your invoices as soon as
          PayPal confirms it.
        </p>
      )}
      {r.resplit && (
        <p className="rounded-card border border-line bg-surface px-4 py-3 text-sm">
          Your split was updated because a balance changed.
        </p>
      )}

      {r.allocations.length > 0 && (
        <section aria-labelledby="applied-h" className="space-y-3">
          <h2 id="applied-h" className="text-lg font-semibold">
            Applied to
          </h2>
          <ul className="divide-y divide-line rounded-card border border-line bg-surface">
            {r.allocations.map((a) => (
              <li key={a.invoice_no} className="px-4 py-3">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="font-medium">{a.invoice_no}</span>
                  <span className="tabular">{formatCents(a.amount_cents)}</span>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-sm">
                  {a.due_after_cents === 0 ? (
                    <StatusChip tone="success">Paid in full</StatusChip>
                  ) : (
                    <StatusChip tone="accent">{formatCents(a.due_after_cents)} left</StatusChip>
                  )}
                  {a.recorded ? (
                    a.paypal_url && (
                      <a
                        href={a.paypal_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-accent underline"
                      >
                        View on PayPal
                      </a>
                    )
                  ) : a.record_state === "FAILED" ? (
                    <span className="text-warning">PayPal invoice not updated yet</span>
                  ) : (
                    <span className="text-muted">Updating on PayPal…</span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {(stuck || r.allocations.some((a) => a.record_state === "FAILED")) && (
        <div className="rounded-card border border-warning/30 bg-warning/5 p-4 text-sm">
          <p>Your payment is safe with us. Some PayPal invoices haven&apos;t shown it yet.</p>
          <button
            type="button"
            disabled={retrying}
            onClick={async () => {
              setRetrying(true);
              try {
                const receipt = await api.refresh(token, id);
                setLoad({ kind: "ready", receipt });
              } catch {
                /* the next manual retry or the server sweep will get it */
              } finally {
                setRetrying(false);
              }
            }}
            className="mt-2 min-h-11 rounded-card bg-accent px-4 font-medium text-white disabled:opacity-60"
          >
            {retrying ? "Retrying…" : "Retry updating PayPal"}
          </button>
        </div>
      )}

      <section className="rounded-card border border-line bg-surface p-5">
        <p className="text-sm text-muted">You now owe</p>
        <p className="tabular text-2xl font-semibold">{formatCents(r.new_owed_cents)}</p>
        {r.credit_cents > 0 && (
          <p className="mt-1 text-sm">
            {formatCents(r.credit_cents)} of this payment is kept as credit on your account.
          </p>
        )}
      </section>

      {back}
    </div>
  );
}
