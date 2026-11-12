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
  agent: "AI",
  seed: "sample",
  fallback: "needs your reply",
  system: "automatic",
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
  if (c.kind === "REMINDER") return "Send reminder on PayPal";
  return "Reply";
}

/** What the decide call returns: the proposal after the decision (status, payload, result). */
export type Decided = Pick<Card, "id" | "status" | "payload" | "result">;

export function ProposalCard({ card, onDone }: { card: Card; onDone: (d: Decided) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [amount, setAmount] = useState(
    card.payload.amount_cents ? (Math.abs(card.payload.amount_cents) / 100).toFixed(2) : "",
  );
  const [mode, setMode] = useState<"APPLY" | "CREDIT" | "SPLIT" | null>(null);
  const [promiseDate, setPromiseDate] = useState(
    typeof card.payload.promise_date === "string" ? card.payload.promise_date : "",
  );
  const isReminder = card.kind === "REMINDER";
  const draftText = typeof card.payload.text === "string" ? card.payload.text : "";
  const [text, setText] = useState(isReminder ? draftText : "");

  const isAdj = card.kind === "ADJUSTMENT";
  const needsText = card.kind === "REPLY" || card.kind === "RETURN_CHECK";
  const failed = card.status === "FAILED";

  const send = (body: DecideBody) => {
    setBusy(true);
    setError(null);
    api.supplier
      .decide(card.id, body)
      .then((d) => onDone(d as Decided))
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
    if (card.kind === "PROMISE_NOTE" && promiseDate && promiseDate !== card.payload.promise_date)
      body.edits = { promise_date: promiseDate };
    if (isReminder && text.trim() !== draftText) {
      if (!text.trim()) return setError("Write the reminder, or reject it");
      body.edits = { text: text.trim() };
    }
    send(body);
  };

  return (
    <article className="min-w-0 break-words rounded-card border border-line bg-surface p-4">
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
            <li key={c} className="max-w-full rounded-full bg-success/10 px-3 py-1 text-xs text-success">
              {c} ✓
            </li>
          ))}
        </ul>
      )}
      {isAdj && card.due_cents !== null && (
        <p className="mt-2 text-xs text-muted">
          {card.suggested === "CREDIT"
            ? `${card.invoice_no} is already paid, so this becomes a credit.`
            : `${card.invoice_no} still owes ${formatCents(card.due_cents)}.`}
        </p>
      )}
      {failed && card.result?.error && (
        <p role="alert" className="mt-2 text-sm text-danger">
          PayPal didn&apos;t take the update ({String(card.result.error)}). Your records are
          already right. Try again to update PayPal.
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
            {(["APPLY", "CREDIT", "SPLIT"] as const).map((m) => (
              <label key={m} className="flex min-h-11 items-center gap-2">
                <input
                  type="radio"
                  name={`mode-${card.id}`}
                  checked={mode === m}
                  onChange={() => setMode(m)}
                />
                {m === "APPLY"
                  ? "Take it off the invoice"
                  : m === "CREDIT"
                    ? "Give it as credit"
                    : `Take ${formatCents(Math.min(card.due_cents ?? 0, Math.abs(card.payload.amount_cents ?? 0)))} off and credit the rest`}
              </label>
            ))}
          </fieldset>
        </div>
      )}
      {card.kind === "PROMISE_NOTE" && !rejecting && card.status === "PENDING" && (
        <label className="mt-3 block text-sm">
          Promised date
          <input
            type="date"
            value={promiseDate}
            onChange={(e) => setPromiseDate(e.target.value)}
            className="mt-1 block min-h-11 rounded-card border border-line px-3"
          />
        </label>
      )}
      {isReminder && !rejecting && (
        <label className="mt-3 block text-sm">
          Reminder (you can edit it)
          <textarea
            value={text}
            maxLength={500}
            rows={4}
            onChange={(e) => setText(e.target.value)}
            className="mt-1 block w-full rounded-card border border-line px-3 py-2"
          />
          {typeof card.payload.send_after === "string" && (
            <span className="text-xs text-muted">Suggested send date {card.payload.send_after}</span>
          )}
        </label>
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
            {card.kind === "RETURN_CHECK" && (
              <>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    send({ decision: "APPROVE", edits: { resolution: "CREDIT_LINE", ...(text.trim() ? { text: text.trim() } : {}) } })
                  }
                  className="min-h-11 rounded-card border border-line px-4"
                >
                  Credit {typeof card.payload.sku === "string" ? card.payload.sku : "the item"}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    send({ decision: "APPROVE", edits: { resolution: "ASK_FOR_SLIP", ...(text.trim() ? { text: text.trim() } : {}) } })
                  }
                  className="min-h-11 rounded-card border border-line px-4"
                >
                  Ask for the return slip
                </button>
              </>
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

/** The outcome in plain words + PayPal's link when the invoice changed (A12). */
export function resultLine(card: Card): { text: string; link: string | null } {
  const r = card.result ?? {};
  if (card.status === "REJECTED")
    return { text: r.reply_text ? `Declined. Note sent: “${r.reply_text}”` : "Declined", link: null };
  if (card.payload.mode === "CREDIT")
    return { text: `Approved as ${formatCents(card.payload.credit_cents ?? 0)} credit`, link: null };
  if (typeof r.paypal_due_cents === "number")
    return {
      text: `PayPal invoice ${card.invoice_no} updated: due ${formatCents(r.paypal_due_cents)}`,
      link: card.paypal_url,
    };
  if (r.reply_text) return { text: `Reply sent: “${r.reply_text}”`, link: null };
  if (card.kind === "PROMISE_NOTE") return { text: "Noted", link: null };
  if (card.kind === "REPLY") return { text: "Marked as seen", link: null };
  if (card.kind === "REMINDER" && card.status === "DONE")
    return { text: `Reminder sent on PayPal for ${card.invoice_no}`, link: card.paypal_url };
  return { text: card.status === "DONE" ? "Done" : card.status.toLowerCase(), link: null };
}

function ResultText({ card }: { card: Card }) {
  const { text, link } = resultLine(card);
  return (
    <>
      {text}
      {link && (
        <>
          {" · "}
          <a href={link} target="_blank" rel="noreferrer" className="text-accent underline">
            View on PayPal
          </a>
        </>
      )}
    </>
  );
}

/** A card the supplier just decided: it stays where it was, with the result (A12). */
export function DecidedCard({ card }: { card: Card }) {
  const ok = card.status !== "REJECTED";
  return (
    <article
      aria-live="polite"
      className={`min-w-0 break-words rounded-card border p-4 ${ok ? "border-success/40 bg-success/5" : "border-line bg-surface"}`}
    >
      <p className="text-sm text-muted">{card.buyer.name}</p>
      <p className="mt-1 font-medium">{card.summary}</p>
      <p className={`mt-2 font-medium ${ok ? "text-success" : ""}`}>
        {ok ? "✓ " : ""}
        <ResultText card={card} />
      </p>
    </article>
  );
}

/** A decided request, for the buyer page's history (A12 result line). */
export function DecidedLine({ card }: { card: Card }) {
  const tone = card.status === "REJECTED" ? "danger" : card.status === "DONE" ? "success" : "muted";
  return (
    <li className="px-4 py-3">
      <p>
        {card.summary}{" "}
        <StatusChip tone={tone}>{card.status === "DONE" ? "Done" : card.status.toLowerCase()}</StatusChip>
      </p>
      <p className="mt-1 text-sm text-muted">
        <ResultText card={card} />
      </p>
    </li>
  );
}
