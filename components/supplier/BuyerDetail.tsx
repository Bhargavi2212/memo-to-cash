"use client";
// Supplier buyer page `/supplier/buyers/[id]` (P3 A3, A12; UI_UX §4.3b): memos as the buyer sees
// them, invoices with PayPal's stored status + mismatch, requests (open ones decidable), messages,
// payments; Copy statement link (FR-15) and Record payment (FR-14).
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { longDate } from "@/lib/dates";
import { paymentMethodLabel, paypalStatusLabel } from "@/lib/labels";
import { formatCents } from "@/lib/money";

import { MemoGroup } from "../MemoGroup";
import { PaypalSetup } from "../PaypalSetup";
import { StatusChip } from "../StatusChip";
import { DecidedLine, ProposalCard } from "./ProposalCard";
import { RecordPaymentModal } from "./RecordPaymentModal";
import { SupplierBanner, useSupplierData } from "./SupplierShell";

export function BuyerDetail() {
  const { id } = useParams<{ id: string }>();
  const [reload, setReload] = useState(0);
  const { data: d, error } = useSupplierData(() => api.supplier.buyer(Number(id)), reload);
  const [copied, setCopied] = useState(false);
  const [recording, setRecording] = useState(false);
  const [drafting, setDrafting] = useState<number | null>(null); // invoice id (P3b B6)
  const [draftError, setDraftError] = useState<{ id: number; message: string } | null>(null);
  // null until known: the banner must not appear after the page and push buttons down
  const [demo, setDemo] = useState<boolean | null>(null);
  useEffect(() => {
    api
      .config()
      .then((c) => setDemo(c.demo_mode))
      .catch(() => setDemo(false));
  }, []);

  if (error) return <p role="alert">{error}</p>;
  if (!d || demo === null) return <div aria-busy="true" className="h-48 animate-pulse rounded-card bg-line" />;
  const open = d.proposals.filter((p) => p.status === "PENDING" || p.status === "FAILED");
  const decided = d.proposals.filter((p) => !(p.status === "PENDING" || p.status === "FAILED"));
  const refresh = () => setReload((n) => n + 1);

  return (
    <div className="min-w-0 space-y-6 break-words">
      {demo && <SupplierBanner token={d.statement_token} />}
      <p className="text-sm">
        <Link href="/supplier" className="text-accent underline">
          ← All buyers
        </Link>
      </p>
      <header className="flex flex-wrap items-end justify-between gap-4 rounded-card border border-line bg-surface p-4">
        <div>
          <h1 className="text-xl font-semibold">{d.buyer.name}</h1>
          <p className="tabular mt-1">
            Owes {formatCents(d.owed_cents)}
            {d.past_due_cents > 0 && (
              <span className="text-warning"> · {formatCents(d.past_due_cents)} past due</span>
            )}
            {d.credit_cents > 0 && <span className="text-muted"> · credit {formatCents(d.credit_cents)}</span>}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() =>
              navigator.clipboard
                .writeText(d.statement_url)
                .then(() => setCopied(true))
                .catch(() => setCopied(false))
            }
            className="min-h-11 rounded-card border border-line px-4"
          >
            {copied ? "Link copied ✓" : "Copy statement link"}
          </button>
          <button
            type="button"
            onClick={() => setRecording(true)}
            disabled={d.paypal_setup !== "READY"}
            className="min-h-11 rounded-card bg-accent px-4 font-medium text-white disabled:opacity-50"
          >
            Record payment
          </button>
        </div>
      </header>
      {d.paypal_setup !== "READY" && (
        <PaypalSetup
          run={() => api.supplier.setup(d.buyer.id)}
          onReady={refresh}
          action="Record payment"
        />
      )}

      {open.length > 0 && (
        <section aria-labelledby="open-h" className="space-y-3">
          <h2 id="open-h" className="text-lg font-semibold">
            Needs your decision ({open.length})
          </h2>
          {open.map((c) => (
            <ProposalCard key={c.id} card={c} onDone={refresh} />
          ))}
        </section>
      )}

      <section aria-labelledby="inv-h" className="overflow-x-auto rounded-card border border-line bg-surface">
        <h2 id="inv-h" className="px-4 pt-4 text-lg font-semibold">
          Invoices
        </h2>
        <ul className="divide-y divide-line">
          {d.invoices.map((i) => (
            <li key={i.id} className="flex flex-wrap items-baseline justify-between gap-2 px-4 py-3 text-sm">
              <span>
                <span className="font-medium">{i.invoice_no}</span>{" "}
                <span className="text-muted">memo {i.memo_no}</span>
                {i.adjustments.map((a) => (
                  <span key={a.name} className="block text-xs text-muted">
                    {a.name} {formatCents(a.amount_cents)}
                  </span>
                ))}
              </span>
              <span className="flex flex-wrap items-center gap-2">
                <span className="tabular">
                  {formatCents(i.due_cents)} due of {formatCents(i.total_cents)}
                </span>
                {i.paypal_status && (
                  <StatusChip tone="muted">PayPal: {paypalStatusLabel(i.paypal_status)}</StatusChip>
                )}
                {i.mismatch && <StatusChip tone="warning">PayPal differs</StatusChip>}
                {i.paypal_url && (
                  <a href={i.paypal_url} target="_blank" rel="noreferrer" className="text-accent underline">
                    View on PayPal
                  </a>
                )}
                {i.reminder === "AVAILABLE" && (
                  <button
                    type="button"
                    disabled={drafting !== null}
                    onClick={() => {
                      setDrafting(i.id);
                      setDraftError(null);
                      api.supplier
                        .draftReminder(i.id)
                        .then(() => refresh())
                        .catch((e: unknown) =>
                          setDraftError({
                            id: i.id,
                            message: e instanceof ApiError ? e.message : "Couldn't draft a reminder — try again.",
                          }),
                        )
                        .finally(() => setDrafting(null));
                    }}
                    className="min-h-11 rounded-card border border-accent px-3 text-accent disabled:opacity-50"
                  >
                    {drafting === i.id ? "Drafting…" : "Draft a reminder"}
                  </button>
                )}
                {i.reminder === "PENDING" && (
                  <span className="text-xs text-muted">Reminder waiting for your decision</span>
                )}
              </span>
              {draftError?.id === i.id && (
                <p role="alert" className="w-full text-sm text-danger">
                  {draftError.message}
                </p>
              )}
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="pays-h" className="rounded-card border border-line bg-surface">
        <h2 id="pays-h" className="px-4 pt-4 text-lg font-semibold">
          Payments
        </h2>
        {d.payments.length === 0 ? (
          <p className="px-4 py-3 text-sm text-muted">No payments yet.</p>
        ) : (
          <ul className="divide-y divide-line">
            {d.payments.map((p) => (
              <li key={p.id} className="px-4 py-3 text-sm">
                <Link href={`/supplier/payments/${p.id}`} className="font-medium text-accent underline">
                  {formatCents(p.amount_cents)} · {paymentMethodLabel(p.source, p.method)}
                </Link>
                <span className="text-muted"> · {longDate(p.received_at)}</span>
                <span className="mt-1 block text-muted">
                  {p.allocations.map((a) => `${a.invoice_no} ${formatCents(a.amount_cents)}`).join(" · ")}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {decided.length > 0 && (
        <section aria-labelledby="hist-h" className="rounded-card border border-line bg-surface">
          <h2 id="hist-h" className="px-4 pt-4 text-lg font-semibold">
            Decided requests
          </h2>
          <ul className="divide-y divide-line">
            {decided.map((c) => (
              <DecidedLine key={c.id} card={c} />
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="msg-h" className="rounded-card border border-line bg-surface">
        <h2 id="msg-h" className="px-4 pt-4 text-lg font-semibold">
          Messages from {d.buyer.name}
        </h2>
        {d.messages.length === 0 && <p className="px-4 py-3 text-sm text-muted">No messages yet.</p>}
        <ul className="divide-y divide-line">
          {d.messages.map((m) => (
            <li key={m.id} className="px-4 py-3 text-sm">
              “{m.text}” <span className="text-muted">· {longDate(m.created_at)}{m.label ? ` · ${m.label.toLowerCase().replace("_", " ")}` : ""}</span>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="memo-h" className="space-y-3">
        <h2 id="memo-h" className="text-lg font-semibold">
          What they have on memo
        </h2>
        {d.memos.map((m) => (
          <MemoGroup key={m.memo_no} memo={m} defaultOpen={false} />
        ))}
      </section>

      {recording && (
        <RecordPaymentModal
          buyerId={d.buyer.id}
          buyerName={d.buyer.name}
          owedCents={d.owed_cents}
          token={d.statement_token}
          onClose={(recorded) => {
            setRecording(false);
            if (recorded) refresh();
          }}
        />
      )}
    </div>
  );
}
