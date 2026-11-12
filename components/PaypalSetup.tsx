"use client";
// A buyer whose invoices aren't on PayPal yet (P3 §M M9): Harbor Gems and Lakeside Fine Jewelry
// are set up a few seconds after "Start the demo". On open this runs (or joins) that setup once
// — the server waits up to 10 s — then reloads the page when ready, or offers Retry. Never a
// dead end; nothing can be paid or recorded until it's READY (the API answers 409 too).
import { useCallback, useEffect, useState } from "react";

import type { SetupResult } from "@/lib/api";

export function PaypalSetup({
  run,
  onReady,
  action,
}: {
  run: () => Promise<SetupResult>;
  onReady: () => void;
  action: string; // "Pay" / "Record payment": what waits for PayPal
}) {
  const [failed, setFailed] = useState(false);
  const attempt = useCallback(
    (live: () => boolean) =>
      run()
        .then((r) => live() && (r.paypal_setup === "READY" ? onReady() : setFailed(true)))
        .catch(() => live() && setFailed(true)),
    [run, onReady],
  );
  const retry = () => {
    setFailed(false);
    void attempt(() => true);
  };

  useEffect(() => {
    let live = true;
    void attempt(() => live);
    return () => {
      live = false;
    };
    // once per open: `attempt` changes identity with its props; the setup itself is idempotent
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div role="status" aria-live="polite" className="rounded-card border border-line bg-surface p-4 text-sm">
      {failed ? (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-danger">Couldn&apos;t set up these invoices on PayPal.</p>
          <button type="button" onClick={retry} className="min-h-11 rounded-card border border-accent px-4 font-medium text-accent">
            Retry
          </button>
        </div>
      ) : (
        <p className="text-muted">
          Setting up on PayPal… {action} opens as soon as these invoices are on PayPal (a few
          seconds).
        </p>
      )}
    </div>
  );
}
