"use client";
// One request for the supplier to decide (P3 A5, A8, A12; P4 R13). Summary + ✓ checks the code
// ran + source badge + the buyer's own words; Approve (label from `suggested` — the mode is
// decided again under the buyer lock, so no mode is sent unless the supplier edits), Edit,
// Reject. FAILED shows PayPal's error and only Retry (the ledger already changed, G7).
import { useState } from "react";

import { ApiError, api, type DecideBody, type ProposalCard as Card } from "@/lib/api";
import { formatCents, parseDollars } from "@/lib/money";

import { StatusChip } from "../StatusChip";

const SOURCE: Record<Card["source"], string> = {
  agent: "agent",
  seed: "seed",
  fallback: "assistant couldn't answer",
};

function approveLabel(c: Card): string {
  const amount = Math.abs(c.payload.amount_cents ?? 0);
  if (c.kind === "ADJUSTMENT") {
    return c.suggested === "CREDIT"
      ? `Approve as ${formatCents(amount)} credit`
      : `Approve — take ${formatCents(amount)} off ${c.invoice_no ?? "the invoice"}`;
  }
  if (c.kind === "PROMISE_NOTE") return "Noted";
  if (c.kind === "REPLY") return "Send reply";
  return "Reply";
}

export function ProposalCard({ card, onDone }: { card: Card; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [amount, setAmount] = useState(
    card.payload.amount_cents ? (Math.abs(card.payload.amount_cents) / 100).toFixed(2) : "",
  );
  const [mode, setMode] = useState<"APPLY" | "CREDIT" | null>(null);
  const [text, setText] = useState("");

  const isAdj = card.kind === "ADJUSTMENT";
  const needsText = card.kind === "REPLY" || card.kind === "RETURN_CHECK";
  const failed = card.status === "FAILED";

  const send = (body: DecideBody) => {
    setBusy(true);
    setError(null);
    api.supplier
      .decide(card.id, body)
      .then(onDone)
      .catch((e: unknown) => {
        setBusy(false);
        setError(e instanceof ApiError ? e.message : "Something went wrong. Please try again.");
      });
  };

  const approve = () => {
    const body: DecideBody = { decision: "APPROVE" };
    if (isAdj && editing) {
      const cents = parseDollars(amount);
      if (cents === null || cents <= 0) return setError("Enter an amount like 15.00");
      body.edits = { amount_cents: cents };
      if (mode) body.mode = mode;
    }
    if (needsText && text.trim()) body.edits = { text: text.trim() };
    send(body);
  };

  return (
    <article className="rounded-card border border-line bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{card.buyer.name}</span>
        <StatusChip tone={card.source === "agent" ? "accent" : "muted"}>
          {SOURCE[card.source]}
        </StatusChip>
        {failed && <StatusChip tone="danger">PayPal update failed</StatusChip>}
      </div>
      {card.message_text && (
        <blockquote className="mt-2 border-l-2 border-line pl-3 text-sm text-muted">
          “{card.message_text}”
        </blockquote>
      )}
      <p className="mt-2 font-medium">{card.summary}</p>
      {card.checks.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-2" aria-label="Checked by code">
          {card.checks.map((c) => (
            <li key={c} className="rounded-full bg-success/10 px-3 py-1 text-xs text-success">
              {c} ✓
            </li>
          ))}
        </ul>
      )}
      {isAdj && card.due_cents !== null && (
        <p className="mt-2 text-xs text-muted">
          {card.invoice_no} has {formatCents(card.due_cents)} due now.
          {card.suggested === "CREDIT" && " It's already paid, so this becomes a credit."}
        </p>
      )}
      {failed && card.result?.error && (
        <p role="alert" className="mt-2 text-sm text-danger">
          PayPal said: {String(card.result.error)}. Our records already show the change — retry
          to update PayPal.
        </p>
      )}

      {editing && isAdj && (
        <div className="mt-3 space-y-2">
          <label className="block text-sm">
            Amount off ($)
            <input
              inputMode="decimal"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              className="mt-1 block min-h-11 w-40 rounded-card border border-line px-3"
            />
          </label>
          <fieldset className="flex gap-4 text-sm">
            <legend className="sr-only">How to apply it</legend>
            {(["APPLY", "CREDIT"] as const).map((m) => (
              <label key={m} className="flex min-h-11 items-center gap-2">
                <input
                  type="radio"
                  name={`mode-${card.id}`}
                  checked={mode === m}
                  onChange={() => setMode(m)}
                />
                {m === "APPLY" ? "Take it off the invoice" : "Give it as credit"}
              </label>
            ))}
          </fieldset>
        </div>
      )}
      {(needsText || rejecting) && (
        <label className="mt-3 block text-sm">
          {rejecting ? "Note to the buyer (optional)" : "Reply to the buyer"}
          <textarea
            value={text}
            maxLength={1000}
            rows={2}
            onChange={(e) => setText(e.target.value)}
            className="mt-1 block w-full rounded-card border border-line px-3 py-2"
          />
        </label>
      )}

      {error && (
        <p role="alert" className="mt-2 text-sm text-danger">
          {error}
        </p>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {rejecting ? (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                send({ decision: "REJECT", edits: text.trim() ? { text: text.trim() } : undefined })
              }
              className="min-h-11 rounded-card bg-danger px-4 font-medium text-white disabled:opacity-50"
            >
              Reject
            </button>
            <button
              type="button"
              onClick={() => setRejecting(false)}
              className="min-h-11 rounded-card border border-line px-4"
            >
              Cancel
            </button>
          </>
        ) : failed ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => send({ decision: "APPROVE" })}
            className="min-h-11 rounded-card bg-accent px-4 font-medium text-white disabled:opacity-50"
          >
            {busy ? "Retrying…" : "Retry PayPal update"}
          </button>
        ) : (
          <>
            <button
              type="button"
              disabled={busy || (card.kind === "RETURN_CHECK" && !text.trim())}
              onClick={approve}
              className="min-h-11 rounded-card bg-accent px-4 font-medium text-white disabled:opacity-50"
            >
              {busy ? "Working…" : approveLabel(card)}
            </button>
            {card.kind === "REPLY" && (
              <button
                type="button"
                disabled={busy}
                onClick={() => send({ decision: "APPROVE" })}
                className="min-h-11 rounded-card border border-line px-4"
              >
                Mark as seen
              </button>
            )}
            {isAdj && !editing && (
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="min-h-11 rounded-card border border-line px-4"
              >
                Edit
              </button>
            )}
            <button
              type="button"
              onClick={() => setRejecting(true)}
              className="min-h-11 rounded-card border border-line px-4"
            >
              Reject
            </button>
          </>
        )}
      </div>
    </article>
  );
}

/** A decided request, for the buyer page's history (A12 result line). */
export function DecidedLine({ card }: { card: Card }) {
  const r = card.result ?? {};
  let detail = "";
  if (card.payload.mode === "CREDIT") detail = `${formatCents(card.payload.credit_cents ?? 0)} credit`;
  else if (typeof r.paypal_due_cents === "number")
    detail = `PayPal invoice ${card.invoice_no} updated: due ${formatCents(r.paypal_due_cents)}`;
  else if (r.reply_text) detail = `Replied: “${r.reply_text}”`;
  const tone = card.status === "REJECTED" ? "danger" : card.status === "DONE" ? "success" : "muted";
  return (
    <li className="px-4 py-3">
      <p>
        {card.summary}{" "}
        <StatusChip tone={tone}>{card.status === "DONE" ? "Done" : card.status.toLowerCase()}</StatusChip>
      </p>
      {detail && (
        <p className="mt-1 text-sm text-muted">
          {detail}
          {card.paypal_url && typeof r.paypal_due_cents === "number" && (
            <>
              {" · "}
              <a href={card.paypal_url} target="_blank" rel="noreferrer" className="text-accent underline">
                View on PayPal
              </a>
            </>
          )}
        </p>
      )}
    </li>
  );
}
