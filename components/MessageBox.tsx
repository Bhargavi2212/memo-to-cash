"use client";
// "Something wrong? Tell Demo Diamond Co." (FR-9, P4 D14). The buyer's text becomes a request
// the supplier decides; what the buyer sees back is written by code — a summary with ✓ check
// chips, or an answer from the records. Demo chips never repeat the seeded S-120 request (R3).
import { useState } from "react";

import { ApiError, api, type MessageResult } from "@/lib/api";

const DEMO_CHIPS = [
  "S-104 has a chipped stone, please take $50 off",
  "I returned S-103 last week",
  "I'll pay the rest on Friday",
];
const MAX = 2000;

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "sent"; result: MessageResult }
  | { kind: "error"; message: string };

export function MessageBox({
  token,
  demoMode,
  onSent,
}: {
  token: string;
  demoMode: boolean;
  onSent: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  const send = (body: string) => {
    const t = body.trim();
    if (!t || state.kind === "loading") return;
    setState({ kind: "loading" });
    api
      .message(token, t)
      .then((result) => {
        setState({ kind: "sent", result });
        setText("");
        onSent();
      })
      .catch((e: unknown) =>
        setState({
          kind: "error",
          message:
            e instanceof ApiError
              ? e.message
              : "Something went wrong. Please try again.",
        }),
      );
  };

  if (!open)
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="min-h-11 w-full rounded-card border border-line bg-surface px-4 text-left font-medium hover:border-accent"
      >
        Something wrong? Tell Demo Diamond Co.
      </button>
    );

  const busy = state.kind === "loading";
  return (
    <section
      aria-labelledby="msg-h"
      className="rounded-card border border-line bg-surface p-4"
    >
      <h2 id="msg-h" className="text-lg font-semibold">
        Tell Demo Diamond Co.
      </h2>
      <p className="mt-1 text-sm text-muted">
        A damaged item, a return, when you&apos;ll pay — they decide; nothing
        changes until they do.
      </p>
      {demoMode && (
        <div className="mt-3 flex flex-wrap gap-2">
          {DEMO_CHIPS.map((c) => (
            <button
              key={c}
              type="button"
              disabled={busy}
              onClick={() => setText(c)}
              className="min-h-11 rounded-full border border-line px-4 text-sm hover:border-accent disabled:opacity-50"
            >
              {c}
            </button>
          ))}
        </div>
      )}
      <form
        className="mt-3 space-y-2"
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
        }}
      >
        <label htmlFor="msg-input" className="sr-only">
          Your message
        </label>
        <textarea
          id="msg-input"
          value={text}
          maxLength={MAX}
          rows={3}
          onChange={(e) => setText(e.target.value)}
          placeholder="What's wrong?"
          className="w-full rounded-card border border-line px-3 py-2"
        />
        <button
          type="submit"
          disabled={busy || !text.trim()}
          className="min-h-11 rounded-card bg-accent px-4 font-medium text-white disabled:opacity-50"
        >
          {busy ? "Sending…" : "Send"}
        </button>
      </form>

      <div aria-live="polite" className="mt-3">
        {busy && <p className="text-sm text-muted">Checking your records…</p>}
        {state.kind === "error" && (
          <p role="alert" className="text-sm text-danger">
            {state.message}
          </p>
        )}
        {state.kind === "sent" && <Sent result={state.result} />}
      </div>
    </section>
  );
}

function Sent({ result }: { result: MessageResult }) {
  const answerOnly = result.answer !== null && result.proposal_ids.length === 0;
  return (
    <div className="rounded-card bg-canvas p-4">
      {answerOnly ? (
        <p>{result.answer}</p>
      ) : (
        <>
          <p>
            <span className="font-medium">Sent:</span> {result.summary}
          </p>
          {result.answer && result.summary !== result.answer && (
            <p className="mt-2">{result.answer}</p>
          )}
        </>
      )}
      {result.checks.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {result.checks.map((c) => (
            <span
              key={c}
              className="rounded-full bg-success/10 px-3 py-1 text-sm text-success"
            >
              {c} ✓
            </span>
          ))}
        </div>
      )}
      {!result.fallback && (
        <p className="mt-3 text-xs text-muted">
          Checked by our assistant against your records
        </p>
      )}
    </div>
  );
}
