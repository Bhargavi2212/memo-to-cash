"use client";
// Buyer statement (FR-3, UI_UX §4.1). Reads the token with useParams() inside the page's
// <Suspense> (P2 R6). Data comes only from our ledger API (no PayPal on page load).
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, api, type PublicConfig, type Statement as StatementData } from "@/lib/api";
import { formatCents } from "@/lib/money";

import { AskBox } from "./AskBox";
import { InvoiceList } from "./InvoiceList";
import { MemoGroup } from "./MemoGroup";
import { MessageBox } from "./MessageBox";
import { Notices } from "./Notices";
import { PayPanel } from "./PayPanel";
import { SeeAsSupplier } from "./SeeAsSupplier";
import { YourRequests } from "./YourRequests";

type Load =
  | { kind: "loading" }
  | { kind: "missing" }
  | { kind: "error"; message: string }
  | { kind: "ready"; statement: StatementData; config: PublicConfig };

export function StatementSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading your statement" className="animate-pulse space-y-4">
      <div className="h-24 rounded-card bg-line" />
      <div className="h-40 rounded-card bg-line" />
      <div className="h-32 rounded-card bg-line" />
    </div>
  );
}

export function Statement() {
  const { token } = useParams<{ token: string }>();
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [changed, setChanged] = useState<string | null>(null);
  const [reload, setReload] = useState(0); // bump to refetch ("Try again", statement changed)
  const [focus, setFocus] = useState<string | null>(null); // record chip target (P4 D14)

  // a record chip was clicked: scroll to it (the memo opens itself) and highlight it. Invoices
  // sit far above the Ask box, so the smooth scroll takes ~1–2 s: hold their highlight 4 s so
  // it stays visible ≥ 1.5 s after arriving (lines are closer: 2.5 s).
  useEffect(() => {
    if (!focus) return;
    const frame = requestAnimationFrame(() =>
      document.getElementById(focus)?.scrollIntoView({ behavior: "smooth", block: "center" }),
    );
    const hold = focus.startsWith("invoice-") ? 4000 : 2500;
    const timer = setTimeout(() => setFocus(null), hold);
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(timer);
    };
  }, [focus]);

  useEffect(() => {
    let live = true;
    Promise.all([api.statement(token), api.config()])
      .then(([statement, config]) => {
        if (!live) return;
        setLoad({ kind: "ready", statement, config });
        // default: every open invoice selected (UI_UX §4.1)
        setSelected(new Set(statement.invoices.filter((i) => i.due_cents > 0).map((i) => i.id)));
      })
      .catch((e: unknown) => {
        if (!live) return;
        if (e instanceof ApiError && e.status === 404) setLoad({ kind: "missing" });
        else
          setLoad({
            kind: "error",
            message: e instanceof ApiError ? e.message : "Something went wrong.",
          });
      });
    return () => {
      live = false;
    };
  }, [token, reload]);

  if (load.kind === "loading") return <StatementSkeleton />;
  if (load.kind === "missing")
    return (
      <div className="rounded-card border border-line bg-surface p-6">
        <h1 className="text-xl font-semibold">This statement link isn&apos;t valid</h1>
        <p className="mt-2 text-muted">Ask your supplier for a new link.</p>
      </div>
    );
  if (load.kind === "error")
    return (
      <div role="alert" className="rounded-card border border-line bg-surface p-6">
        <p>{load.message}</p>
        <button
          type="button"
          onClick={() => {
            setLoad({ kind: "loading" });
            setReload((n) => n + 1);
          }}
          className="mt-3 min-h-11 rounded-card bg-accent px-4 font-medium text-white"
        >
          Try again
        </button>
      </div>
    );

  const { statement: s, config } = load;
  const open = s.invoices.filter((i) => i.due_cents > 0);
  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="space-y-6">
      <header className="rounded-card border border-line bg-surface p-5">
        <p className="text-sm font-medium text-accent">{s.supplier.name}</p>
        <h1 className="mt-1 text-lg font-semibold">Statement for {s.buyer.name}</h1>
        <p className="mt-4 text-sm text-muted">You owe</p>
        <p className="tabular text-3xl font-semibold">{formatCents(s.owed_cents)}</p>
        <p className="mt-1 text-sm text-muted">
          {open.length === 0 ? "All paid" : `${open.length} ${open.length === 1 ? "invoice" : "invoices"}`}
          {open.length > 0 && ` · oldest ${s.oldest_age_days} days`}
          {s.past_due_cents > 0 && (
            <span className="text-warning"> · {formatCents(s.past_due_cents)} past due</span>
          )}
        </p>
        {s.credit_cents > 0 && (
          <p className="mt-2 text-sm">
            Credit <span className="tabular">{formatCents(s.credit_cents)}</span>
            <span className="block text-xs text-muted">
              {s.supplier.name} will apply it to a future invoice or refund it in PayPal.
            </span>
          </p>
        )}
        <a
          href="#ask"
          onClick={(e) => {
            // the AI is visible without scrolling past the payment section
            e.preventDefault();
            document.getElementById("ask")?.scrollIntoView({ behavior: "smooth", block: "start" });
            document.getElementById("ask-input")?.focus({ preventScroll: true });
          }}
          className="mt-3 inline-flex min-h-11 items-center text-sm font-medium text-accent underline-offset-4 hover:underline"
        >
          Ask about this statement →
        </a>
        <div className="mt-2">
          <SeeAsSupplier token={token} />
        </div>
      </header>

      {changed && (
        <p role="alert" className="rounded-card border border-warning/30 bg-warning/5 px-4 py-3 text-sm">
          {changed}
        </p>
      )}
      <Notices notices={s.notices} />

      <section aria-labelledby="memos-h" className="space-y-3">
        <h2 id="memos-h" className="text-lg font-semibold">
          What you have on memo
        </h2>
        {s.memos.map((m, i) => (
          <MemoGroup key={m.memo_no} memo={m} defaultOpen={i === 0} focus={focus} />
        ))}
      </section>

      <section aria-labelledby="inv-h" className="space-y-3">
        <h2 id="inv-h" className="text-lg font-semibold">
          Invoices
        </h2>
        <InvoiceList
          invoices={s.invoices}
          selected={selected}
          onToggle={toggle}
          disabled={false}
          focus={focus}
        />
      </section>

      {open.length === 0 ? (
        <p className="rounded-card border border-line bg-surface p-6 text-center font-medium">
          Nothing owed — thank you.
        </p>
      ) : (
        <PayPanel
          key={s.invoices.map((i) => `${i.id}:${i.due_cents}`).join(",")}
          token={token}
          statement={s}
          selected={selected}
          clientId={config.paypal_client_id}
          demoMode={config.demo_mode}
          onChanged={(message) => {
            setChanged(message);
            setReload((n) => n + 1);
          }}
        />
      )}

      <AskBox token={token} statement={s} onFocusRecord={setFocus} />
      <MessageBox
        token={token}
        demoMode={config.demo_mode}
        onSent={() => setReload((n) => n + 1)}
      />
      <YourRequests requests={s.requests} highlighted={focus === "requests"} />
    </div>
  );
}
