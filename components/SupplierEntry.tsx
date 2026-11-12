"use client";
// Landing "See the supplier side" (P3 A10, APP_FLOW J1): with a demo copy → /supplier; without
// one, start a copy first (each visitor only ever sees their own copy as the supplier).
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiError, api } from "@/lib/api";
import { useHydrated } from "@/lib/useHydrated";

export function SupplierEntry() {
  const router = useRouter();
  const ready = useHydrated();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function go() {
    setBusy(true);
    setError(null);
    try {
      const { statement_token } = await api.whoami();
      if (!statement_token) await api.startDemo();
      router.push("/supplier");
    } catch (e) {
      setBusy(false);
      setError(e instanceof ApiError ? e.message : "We couldn't start the demo. Please try again.");
    }
  }

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={go}
        disabled={busy || !ready}
        className="min-h-11 rounded-card border border-accent px-5 font-medium text-accent disabled:opacity-60"
      >
        {busy ? "Opening…" : "See the supplier side"}
      </button>
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
    </div>
  );
}
