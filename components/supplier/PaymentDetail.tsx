"use client";
// Supplier payment page `/supplier/payments/[id]` (P3 A4, A12; UI_UX §4.4): the split with the
// stored PayPal status + "last synced" time, and the timeline. The live PayPal fetch is P3b.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { api } from "@/lib/api";
import { longDate } from "@/lib/dates";
import { paymentMethodLabel, paypalStatusLabel } from "@/lib/labels";
import { formatCents } from "@/lib/money";

import { StatusChip } from "../StatusChip";
import { useSupplierData } from "./SupplierShell";

const ACTION: Record<string, string> = {
  "payment.captured": "Captured by PayPal",
  "payment.capture_pending": "PayPal is holding the payment",
  "payment.recorded": "Recorded by Demo Diamond Co.",
  "payment.voided": "Didn't go through — nothing charged",
  "allocation.recorded": "Marked paid on PayPal",
};

export function PaymentDetail() {
  const { id } = useParams<{ id: string }>();
  const [reload] = useState(0);
  const { data: d, error } = useSupplierData(() => api.supplier.payment(Number(id)), reload);
  if (error) return <p role="alert">{error}</p>;
  if (!d) return <div aria-busy="true" className="h-48 animate-pulse rounded-card bg-line" />;
  const p = d.payment;
  return (
    <div className="space-y-6">
      <p className="text-sm">
        <Link href={`/supplier/buyers/${d.buyer.id}`} className="text-accent underline">
          ← {d.buyer.name}
        </Link>
      </p>
      <header className="rounded-card border border-line bg-surface p-4">
        <h1 className="tabular text-xl font-semibold">{formatCents(p.amount_cents)}</h1>
        <p className="mt-1 text-sm text-muted">
          {longDate(p.received_at)} ·{" "}
          {p.source === "OFF_PAYPAL"
            ? `${paymentMethodLabel(p.source, p.method)}${p.reference ? ` ${p.reference}` : ""}`
            : `PayPal capture ${p.paypal_capture_id ?? "—"}`}
          {p.unapplied_cents > 0 && ` · ${formatCents(p.unapplied_cents)} kept as credit`}
        </p>
      </header>

      <section aria-labelledby="split-h" className="rounded-card border border-line bg-surface">
        <h2 id="split-h" className="px-4 pt-4 text-lg font-semibold">
          Split
        </h2>
        <ul className="divide-y divide-line sm:hidden">
          {d.allocations.map((a) => (
            <li key={a.invoice_id} className="px-4 py-3 text-sm">
              <p>
                <span className="font-medium">{a.invoice_no}</span>{" "}
                <span className="tabular">{formatCents(a.amount_cents)}</span>
              </p>
              <p className="text-muted">
                {a.record_state === "RECORDED" ? "Recorded on PayPal" : a.record_state.toLowerCase()}
                {a.paypal_status && ` · ${paypalStatusLabel(a.paypal_status)}`}
              </p>
              {a.paypal_url && (
                <a href={a.paypal_url} target="_blank" rel="noreferrer" className="text-accent underline">
                  View on PayPal
                </a>
              )}
            </li>
          ))}
        </ul>
        <table className="mt-2 hidden w-full text-sm sm:table">
          <thead className="text-left text-muted">
            <tr>
              <th className="px-3 py-2 font-medium">Invoice</th>
              <th className="px-3 py-2 text-right font-medium">Amount</th>
              <th className="px-3 py-2 font-medium">On PayPal</th>
              <th className="px-3 py-2 font-medium" />
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {d.allocations.map((a) => (
              <tr key={a.invoice_id}>
                <td className="px-3 py-2">{a.invoice_no}</td>
                <td className="tabular px-3 py-2 text-right">{formatCents(a.amount_cents)}</td>
                <td className="px-3 py-2">
                  <StatusChip tone={a.record_state === "RECORDED" ? "success" : a.record_state === "FAILED" ? "danger" : "muted"}>
                    {a.record_state === "RECORDED" ? "recorded" : a.record_state.toLowerCase()}
                  </StatusChip>{" "}
                  {a.paypal_status && (
                    <span className="text-xs text-muted">
                      {paypalStatusLabel(a.paypal_status)}
                      {a.paypal_synced_at && ` · last synced ${new Date(a.paypal_synced_at).toLocaleString()}`}
                    </span>
                  )}
                  {a.mismatch && <span className="block text-xs text-warning">{a.mismatch}</span>}
                </td>
                <td className="px-3 py-2">
                  {a.paypal_url && (
                    <a href={a.paypal_url} target="_blank" rel="noreferrer" className="text-accent underline">
                      View on PayPal
                    </a>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section aria-labelledby="tl-h" className="rounded-card border border-line bg-surface p-4">
        <h2 id="tl-h" className="text-lg font-semibold">
          Timeline
        </h2>
        <ol className="mt-2 space-y-1 text-sm">
          {d.timeline.map((e, i) => (
            <li key={i}>
              <span className="text-muted">{new Date(e.at).toLocaleString()}</span> ·{" "}
              {ACTION[e.action] ?? e.action}
              {e.invoice_no && ` ${e.invoice_no}`}
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
