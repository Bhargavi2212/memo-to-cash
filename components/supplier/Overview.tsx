"use client";
// Supplier overview `/supplier` (P3 A2, A12; UI_UX §4.3, §6): totals, buyers (all real, P3 §M),
// "Needs your decision" and recent payments with their split. Ledger only.
// A decided card stays in place with its result ("PayPal invoice INV-1051 updated: due
// $1,400.00 · View on PayPal") — the judged moment. Tables become stacked cards below 640 px.
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { api, type ProposalCard as Card, type SupplierOverview } from "@/lib/api";
import { longDate } from "@/lib/dates";
import { paymentMethodLabel } from "@/lib/labels";
import { formatCents } from "@/lib/money";

import { type Decided, DecidedCard, ProposalCard } from "./ProposalCard";
import { SupplierBanner, useSupplierData } from "./SupplierShell";

type Buyer = SupplierOverview["buyers"][number];

function oldest(b: Buyer): string {
  return b.owed_cents > 0 ? `${b.oldest_age_days}d${b.past_due_cents > 0 ? " ⚠" : ""}` : "—";
}

/** A buyer not yet on PayPal (P3 §M M9): the row still opens; the buyer page finishes or retries. */
function SetupNote({ b, gaveUp }: { b: Buyer; gaveUp: boolean }) {
  if (b.paypal_setup === "READY") return null;
  return b.paypal_setup === "FAILED" || gaveUp ? (
    <span className="block text-xs text-danger">Not on PayPal yet. Open it to retry.</span>
  ) : (
    <span className="block text-xs text-muted">Setting up on PayPal…</span>
  );
}

const POLL_MS = 2000; // refetch while a buyer is setting up (P3 §M M9)…
const POLL_FOR_MS = 30000; // …for at most 30 s; then the row offers retry on its buyer page

export function Overview() {
  const [reload, setReload] = useState(0);
  const { data, error } = useSupplierData(api.supplier.overview, reload);
  // null until known: the banner must not appear after the page and push buttons down
  const [demo, setDemo] = useState<boolean | null>(null);
  const [decided, setDecided] = useState<Card[]>([]);
  const opened = useRef<number | null>(null);
  const [gaveUp, setGaveUp] = useState(false);
  const settingUp = data?.buyers.some((b) => b.paypal_setup === "SETTING_UP") ?? false;
  useEffect(() => {
    if (!settingUp || gaveUp) return;
    const t = setTimeout(() => {
      opened.current ??= Date.now() - POLL_MS;
      if (Date.now() - opened.current >= POLL_FOR_MS) setGaveUp(true);
      else setReload((n) => n + 1);
    }, POLL_MS);
    return () => clearTimeout(t);
  }, [settingUp, gaveUp, data]);
  useEffect(() => {
    api
      .config()
      .then((c) => setDemo(c.demo_mode))
      .catch(() => setDemo(false));
  }, []);

  if (error) return <p role="alert">{error}</p>;
  if (!data || demo === null)
    return (
      <div aria-busy="true" className="animate-pulse space-y-4">
        <div className="h-16 rounded-card bg-line" />
        <div className="h-48 rounded-card bg-line" />
      </div>
    );

  const onDecided = (card: Card) => (d: Decided) => {
    // FAILED stays in the queue (Retry); anything else stays visible here with its result
    if (d.status !== "FAILED")
      setDecided((prev) => [{ ...card, ...d }, ...prev.filter((x) => x.id !== card.id)]);
    setReload((n) => n + 1);
  };
  const shown = new Set(decided.map((c) => c.id));
  const pending = data.pending.filter((c) => !shown.has(c.id));
  const t = data.totals;

  return (
    <div className="space-y-6">
      {demo && <SupplierBanner />}
      <header className="grid grid-cols-3 gap-3 rounded-card border border-line bg-surface p-4">
        {[
          ["Open", t.open_cents],
          ["Past due", t.past_due_cents],
          ["Paid this week", t.paid_this_week_cents],
        ].map(([label, v]) => (
          <div key={label as string} className="min-w-0">
            <p className="text-xs text-muted sm:text-sm">{label}</p>
            <p className="tabular text-base font-semibold sm:text-xl">{formatCents(v as number)}</p>
          </div>
        ))}
      </header>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="min-w-0 space-y-6">
          <section aria-labelledby="buyers-h" className="rounded-card border border-line bg-surface">
            <h2 id="buyers-h" className="px-4 pt-4 text-lg font-semibold">
              Buyers
            </h2>
            {/* phone: stacked cards (UI_UX §6) */}
            <ul className="divide-y divide-line sm:hidden">
              {data.buyers.map((b) => (
                <li key={b.id} className="px-4 py-3 text-sm">
                  <Link href={`/supplier/buyers/${b.id}`} className="font-medium text-accent underline">
                    {b.name}
                  </Link>
                  <SetupNote b={b} gaveUp={gaveUp} />
                  <p className="tabular mt-1">
                    Owes {formatCents(b.owed_cents)} · oldest {oldest(b)}
                  </p>
                  <p className="text-muted">
                    {b.out} with them · {b.kept} kept · {b.returned} returned
                    {b.promise && ` · promised ${longDate(b.promise)}`}
                  </p>
                </li>
              ))}
            </ul>
            <table className="mt-2 hidden w-full text-sm sm:table">
              <thead className="text-left text-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">Buyer</th>
                  <th className="px-3 py-2 text-right font-medium">Out</th>
                  <th className="px-3 py-2 text-right font-medium">Kept</th>
                  <th className="px-3 py-2 text-right font-medium">Ret.</th>
                  <th className="px-3 py-2 text-right font-medium">Owed</th>
                  <th className="px-3 py-2 text-right font-medium">Oldest</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {data.buyers.map((b) => (
                  <tr key={b.id}>
                    <td className="px-3 py-2">
                      <Link href={`/supplier/buyers/${b.id}`} className="font-medium text-accent underline">
                        {b.name}
                      </Link>
                      <SetupNote b={b} gaveUp={gaveUp} />
                      {b.promise && (
                        <span className="block text-xs text-muted">Promised {longDate(b.promise)}</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right">{b.out}</td>
                    <td className="px-3 py-2 text-right">{b.kept}</td>
                    <td className="px-3 py-2 text-right">{b.returned}</td>
                    <td className="tabular px-3 py-2 text-right">{formatCents(b.owed_cents)}</td>
                    <td className={`tabular px-3 py-2 text-right ${b.past_due_cents > 0 ? "text-warning" : ""}`}>
                      {oldest(b)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.buyers.length === 0 && (
              <p className="px-4 pb-4 text-sm text-muted">No buyers yet.</p>
            )}
          </section>

          <section aria-labelledby="pay-h" className="rounded-card border border-line bg-surface">
            <h2 id="pay-h" className="px-4 pt-4 text-lg font-semibold">
              Recent payments
            </h2>
            {data.recent_payments.length === 0 ? (
              <p className="px-4 py-3 text-sm text-muted">No payments yet.</p>
            ) : (
              <ul className="divide-y divide-line">
                {data.recent_payments.map((p) => (
                  <li key={p.id} className="min-w-0 break-words px-4 py-3 text-sm">
                    <Link href={`/supplier/payments/${p.id}`} className="font-medium text-accent underline">
                      {formatCents(p.amount_cents)} from {p.buyer.name}
                    </Link>
                    <span className="text-muted">
                      {" "}
                      · {longDate(p.received_at)} · {paymentMethodLabel(p.source, p.method)}
                    </span>
                    <span className="mt-1 flex flex-wrap gap-2">
                      {p.split.map((s) => (
                        <span key={s.invoice_no} className="rounded-full bg-line px-2 py-0.5 text-xs">
                          {s.invoice_no} {formatCents(s.amount_cents)}
                        </span>
                      ))}
                      <span className={`text-xs ${p.recorded_all ? "text-success" : "text-warning"}`}>
                        {p.recorded_all ? "on PayPal ✓" : "updating PayPal…"}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        <section aria-labelledby="decide-h" className="min-w-0 space-y-3">
          <h2 id="decide-h" className="text-lg font-semibold">
            Needs your decision ({pending.length})
          </h2>
          {decided.map((c) => (
            <DecidedCard key={`d-${c.id}`} card={c} />
          ))}
          {pending.length === 0 && decided.length === 0 && (
            <p className="text-sm text-muted">Nothing to decide. You&apos;re all caught up.</p>
          )}
          {pending.map((c) => (
            <ProposalCard key={c.id} card={c} onDone={onDecided(c)} />
          ))}
        </section>
      </div>
    </div>
  );
}
