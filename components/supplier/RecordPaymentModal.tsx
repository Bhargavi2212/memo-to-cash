"use client";
// "Record payment" (P3 A9, FR-14; UI_UX §4.3b): a cheque / wire / Zelle the supplier received.
// Live split preview uses the buyer's own statement preview (G12 — same rule, oldest due first);
// one `client_uid` per opened form makes a double click a no-op.
import { useEffect, useId, useState } from "react";

import { ApiError, api, type Preview, type RecordResult } from "@/lib/api";
import { paymentMethodLabel } from "@/lib/labels";
import { formatCents, parseDollars } from "@/lib/money";

const METHODS = [
  ["CHECK", "Cheque"],
  ["WIRE_TRANSFER", "Wire"],
  ["OTHER", "Other (Zelle…)"],
] as const;

export function RecordPaymentModal({
  buyerId,
  buyerName,
  owedCents,
  token,
  onClose,
}: {
  buyerId: number;
  buyerName: string;
  owedCents: number;
  token: string;
  onClose: (recorded: boolean) => void;
}) {
  const ids = useId();
  const [uid] = useState(() => crypto.randomUUID());
  const [method, setMethod] = useState<"CHECK" | "WIRE_TRANSFER" | "OTHER">("CHECK");
  const [reference, setReference] = useState("");
  const [amountText, setAmountText] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<RecordResult | null>(null);
  const amount = parseDollars(amountText);
  // overpayments as credit aren't supported in v1 (P3 §8): say so with the real numbers
  const over = amount !== null && amount > owedCents;
  const overText = over
    ? `That's ${formatCents(amount - owedCents)} more than ${buyerName} owes (${formatCents(owedCents)}). ` +
      `Enter ${formatCents(owedCents)} or less — recording overpayments as credit isn't supported yet.`
    : null;

  useEffect(() => {
    if (amount === null || amount <= 0 || over) return;
    let live = true;
    const t = setTimeout(() => {
      api
        .preview(token, { amount_cents: amount })
        .then((p) => {
          if (!live) return;
          setPreview(p);
          setError(null);
        })
        .catch((e: unknown) => {
          if (!live) return;
          setPreview(null);
          setError(e instanceof ApiError ? e.message : "Couldn't preview the split.");
        });
    }, 200);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [amount, over, token]);

  const confirm = () => {
    if (amount === null || amount <= 0) return setError("Enter the amount received, like 500.00");
    if (over) return;
    setBusy(true);
    setError(null);
    api.supplier
      .record({ buyer_id: buyerId, client_uid: uid, method, reference, amount_cents: amount })
      .then(setDone)
      .catch((e: unknown) => {
        setBusy(false);
        setError(e instanceof ApiError ? e.message : "Something went wrong. Please try again.");
      });
  };

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="rec-h" className="fixed inset-0 z-10 flex items-end justify-center bg-ink/30 p-4 sm:items-center">
      <div className="w-full max-w-md rounded-card bg-surface p-5 shadow-lg">
        <h2 id="rec-h" className="text-lg font-semibold">
          Record a payment
        </h2>
        {done ? (
          <div className="mt-3 space-y-2 text-sm">
            <p>
              Recorded {formatCents(done.amount_cents)} ({paymentMethodLabel("OFF_PAYPAL", done.method).toLowerCase()}
              {done.reference ? ` ${done.reference}` : ""}). It&apos;s being marked on each
              PayPal invoice:
            </p>
            <ul className="space-y-1">
              {done.allocations.map((a) => (
                <li key={a.invoice_no}>
                  {a.invoice_no} {formatCents(a.amount_cents)}
                </li>
              ))}
            </ul>
            <button type="button" onClick={() => onClose(true)} className="mt-2 min-h-11 rounded-card bg-accent px-4 font-medium text-white">
              Done
            </button>
          </div>
        ) : (
          <div className="mt-3 space-y-3 text-sm">
            <div>
              <label htmlFor={`${ids}-m`} className="block">
                Method
              </label>
              <select id={`${ids}-m`} value={method} onChange={(e) => setMethod(e.target.value as typeof method)} className="mt-1 block min-h-11 w-full rounded-card border border-line px-3">
                  {METHODS.map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
              </select>
            </div>
            <div>
              <label htmlFor={`${ids}-r`} className="block">
                Reference (cheque no., wire ref.)
              </label>
              <input id={`${ids}-r`} value={reference} maxLength={100} onChange={(e) => setReference(e.target.value)} className="mt-1 block min-h-11 w-full rounded-card border border-line px-3" />
            </div>
            <div>
              <label htmlFor={`${ids}-a`} className="block">
                Amount received ($)
              </label>
              <input id={`${ids}-a`} inputMode="decimal" value={amountText} onChange={(e) => setAmountText(e.target.value)} className="mt-1 block min-h-11 w-full rounded-card border border-line px-3" />
            </div>
            {overText && (
              <p role="alert" className="text-danger">
                {overText}
              </p>
            )}
            {amount !== null && amount > 0 && !over && (
              <div aria-live="polite" className="rounded-card border border-line bg-canvas p-3">
                <p className="text-xs font-medium text-muted">Split, oldest due first</p>
                {preview && preview.total_cents === amount ? (
                  <ul className="mt-1 space-y-1">
                    {preview.allocations.map((a) => {
                      const left = preview.remaining_after.find((x) => x.invoice_no === a.invoice_no);
                      return (
                        <li key={a.invoice_no} className="flex justify-between gap-3">
                          <span>{a.invoice_no}</span>
                          <span className="tabular">
                            {formatCents(a.amount_cents)}
                            <span className="text-muted">
                              {left && left.due_cents > 0 ? ` · ${formatCents(left.due_cents)} left` : " · paid in full"}
                            </span>
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                ) : (
                  !error && <p className="mt-1 text-muted">Working out the split…</p>
                )}
              </div>
            )}
            {error && !over && (
              <p role="alert" className="text-danger">
                {error}
              </p>
            )}
            <div className="flex gap-2">
              <button type="button" disabled={busy || over} onClick={confirm} className="min-h-11 rounded-card bg-accent px-4 font-medium text-white disabled:opacity-50">
                {busy ? "Recording…" : "Confirm"}
              </button>
              <button type="button" onClick={() => onClose(false)} className="min-h-11 rounded-card border border-line px-4">
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
