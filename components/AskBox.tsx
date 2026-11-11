"use client";
// "Ask about this statement" (FR-8, P4 D14, UI_UX §4.1). Two suggestion chips, a question
// box, and an answer card whose record chips scroll to and highlight the line or invoice.
// Every answer was checked by code against the records; a fallback is shown plainly.
import { useState } from "react";

import { ApiError, api, type AskResult, type Statement } from "@/lib/api";
import { recordChip } from "@/lib/records";

const CHIPS = ["Why is S-102 on my bill?", "What do I owe for M-207?"];
const MAX = 500;

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "answer"; question: string; result: AskResult }
  | { kind: "error"; message: string };

export function AskBox({
  token,
  statement,
  onFocusRecord,
}: {
  token: string;
  statement: Statement;
  onFocusRecord: (target: string) => void;
}) {
  const [text, setText] = useState("");
  const [state, setState] = useState<State>({ kind: "idle" });

  const ask = (question: string) => {
    const q = question.trim();
    if (!q || state.kind === "loading") return;
    setState({ kind: "loading" });
    api
      .ask(token, q)
      .then((result) => {
        setState({ kind: "answer", question: q, result });
        setText("");
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

  const busy = state.kind === "loading";
  return (
    <section
      aria-labelledby="ask-h"
      className="rounded-card border border-line bg-surface p-4"
    >
      <h2 id="ask-h" className="text-lg font-semibold">
        Ask about this statement
      </h2>
      <div className="mt-3 flex flex-wrap gap-2">
        {CHIPS.map((c) => (
          <button
            key={c}
            type="button"
            disabled={busy}
            onClick={() => ask(c)}
            className="min-h-11 rounded-full border border-line px-4 text-sm hover:border-accent disabled:opacity-50"
          >
            {c}
          </button>
        ))}
      </div>
      <form
        className="mt-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          ask(text);
        }}
      >
        <label htmlFor="ask-input" className="sr-only">
          Your question
        </label>
        <input
          id="ask-input"
          value={text}
          maxLength={MAX}
          onChange={(e) => setText(e.target.value)}
          placeholder="Type a question…"
          className="min-h-11 min-w-0 flex-1 rounded-card border border-line px-3"
        />
        <button
          type="submit"
          disabled={busy || !text.trim()}
          className="min-h-11 min-w-11 rounded-card bg-accent px-4 font-medium text-white disabled:opacity-50"
          aria-label="Ask"
        >
          ➤
        </button>
      </form>

      <div aria-live="polite" className="mt-3">
        {busy && <p className="text-sm text-muted">Checking your records…</p>}
        {state.kind === "error" && (
          <p role="alert" className="text-sm text-danger">
            {state.message}
          </p>
        )}
        {state.kind === "answer" && (
          <Answer
            question={state.question}
            result={state.result}
            statement={statement}
            onFocusRecord={onFocusRecord}
          />
        )}
      </div>
    </section>
  );
}

function Answer({
  question,
  result,
  statement,
  onFocusRecord,
}: {
  question: string;
  result: AskResult;
  statement: Statement;
  onFocusRecord: (target: string) => void;
}) {
  const chips = result.looked_up.map((id) => recordChip(id, statement));
  return (
    <div className="rounded-card bg-canvas p-4">
      <p className="text-sm text-muted">{question}</p>
      <p className="mt-2">{result.answer}</p>
      {!result.fallback && chips.length > 0 && (
        <>
          <div className="mt-3 flex flex-wrap gap-2">
            {chips.map((c) =>
              c.target ? (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => onFocusRecord(c.target as string)}
                  className="min-h-9 rounded-full bg-accent/10 px-3 text-sm text-accent hover:bg-accent/20"
                >
                  {c.label}
                </button>
              ) : (
                <span
                  key={c.id}
                  className="rounded-full bg-line px-3 py-1 text-sm text-muted"
                >
                  {c.label}
                </span>
              ),
            )}
          </div>
          <p className="mt-3 text-xs text-muted">
            Looked up: {chips.map((c) => c.label.split(" · ")[0]).join(" · ")} ·
            Checked by our assistant against your records
          </p>
        </>
      )}
    </div>
  );
}
