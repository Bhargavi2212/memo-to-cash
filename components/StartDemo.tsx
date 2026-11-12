"use client";
// "Start the demo" / "Start again" (P2 D17, UI_UX §4.5, FR-12): each click makes the
// visitor's own fresh copy with real PayPal sandbox invoices, then opens its statement.
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiError, api } from "@/lib/api";
import { useHydrated } from "@/lib/useHydrated";

export function StartDemo() {
  const router = useRouter();
  const ready = useHydrated();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [started, setStarted] = useState(false);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const d = await api.startDemo();
      setStarted(true);
      router.push(`/s/${d.statement_token}`);
    } catch (e) {
      setBusy(false);
      setError(
        e instanceof ApiError ? e.message : "We couldn't start the demo. Please try again.",
      );
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-3">
        <button
          type="button"
          onClick={start}
          disabled={busy || !ready}
          className="min-h-11 rounded-card bg-accent px-5 font-medium text-white disabled:opacity-60"
        >
          {busy ? "Starting…" : started ? "Start again" : "Start the demo"}
        </button>
        {!busy && error && (
          <button
            type="button"
            onClick={start}
            className="min-h-11 rounded-card border border-line bg-surface px-5 font-medium"
          >
            Retry
          </button>
        )}
      </div>
      <p aria-live="polite" className="text-sm text-muted">
        {busy
          ? "Creating your PayPal sandbox invoices… (~2–5 s)"
          : "Your own copy — every click makes a fresh one; the old one is left behind."}
      </p>
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
