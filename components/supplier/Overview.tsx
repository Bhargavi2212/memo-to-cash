"use client";
// Supplier overview `/supplier` (P3 A2, A12; UI_UX §4.3): totals, buyers (+ sample rows in demo
// mode), "Needs your decision" and recent payments with their split. Ledger only.
import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { formatCents } from "@/lib/money";

import { ProposalCard } from "./ProposalCard";
import { SampleRows, SupplierBanner, useSupplierData } from "./SupplierShell";

export function Overview() {
  const [reload, setReload] = useState(0);
  const { data, error } = useSupplierData(api.supplier.overview, reload);
  const [demo, setDemo] = useState(false);
  useEffect(() => {
    api
      .config()
      .then((c) => setDemo(c.demo_mode))
      .catch(() => undefined);
  }, []);

  if (error) return <p role="alert">{error}</p>;
  if (!data)
    return (
      <div aria-busy="true" className="animate-pulse space-y-4">
        <div className="h-16 rounded-card bg-line" />
        <div className="h-48 rounded-card bg-line" />
      </div>
    );
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
          <div key={label as string}>
            <p className="text-sm text-muted">{label}</p>
            <p className="tabular text-xl font-semibold">{formatCents(v as number)}</p>
          </div>
        ))}
      </header>

      <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
        <div className="space-y-6">
          <section aria-labelledby="buyers-h" className="overflow-x-auto rounded-card border border-line bg-surface">
            <h2 id="buyers-h" className="px-4 pt-4 text-lg font-semibold">
              Buyers
            </h2>
            <table className="mt-2 w-full text-sm">
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
                      {b.promise && <span className="block text-xs text-muted">Promised {b.promise}</span>}
                    </td>
                    <td className="px-3 py-2 text-right">{b.out}</td>
                    <td className="px-3 py-2 text-right">{b.kept}</td>
                    <td className="px-3 py-2 text-right">{b.returned}</td>
                    <td className="tabular px-3 py-2 text-right">{formatCents(b.owed_cents)}</td>
                    <td className={`tabular px-3 py-2 text-right ${b.past_due_cents > 0 ? "text-warning" : ""}`}>
                      {b.oldest_age_days}d{b.past_due_cents > 0 && " ⚠"}
                    </td>
                  </tr>
                ))}
                {demo && <SampleRows />}
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
                  <li key={p.id} className="px-4 py-3 text-sm">
                    <Link href={`/supplier/payments/${p.id}`} className="font-medium text-accent underline">
                      {formatCents(p.amount_cents)} from {p.buyer.name}
                    </Link>
                    <span className="text-muted">
                      {" "}
                      · {p.received_at} · {p.source === "OFF_PAYPAL" ? p.method?.toLowerCase() : "PayPal"}
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

        <section aria-labelledby="decide-h" className="space-y-3">
          <h2 id="decide-h" className="text-lg font-semibold">
            Needs your decision ({data.pending.length})
          </h2>
          {data.pending.length === 0 && (
            <p className="text-sm text-muted">Nothing waiting — you&apos;re all caught up.</p>
          )}
          {data.pending.map((c) => (
            <ProposalCard key={c.id} card={c} onDone={() => setReload((n) => n + 1)} />
          ))}
        </section>
      </div>
    </div>
  );
}
