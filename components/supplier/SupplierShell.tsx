"use client";
// Shared bits of the supplier pages (P3 A10, A12, G14): the thin banner back to the buyer's
// statement, the 401 → landing redirect, and the static sample rows (UI_UX §4.3: a frontend
// constant, demo mode only, "sample — not on PayPal", not counted, not clickable).
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";
import { formatCents } from "@/lib/money";

export function SupplierBanner() {
  const [token, setToken] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api
      .whoami()
      .then((w) => live && setToken(w.statement_token))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);
  return (
    <p className="rounded-card bg-accent/5 px-4 py-2 text-sm">
      Supplier view of your demo copy
      {token && (
        <>
          {" · "}
          <Link href={`/s/${token}`} className="font-medium text-accent underline">
            Back to the buyer&apos;s statement
          </Link>
        </>
      )}
    </p>
  );
}

/** Loads a supplier API call; no access (401) sends the visitor to the landing page (G14). */
export function useSupplierData<T>(load: () => Promise<T>, reload: number) {
  const router = useRouter();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    load()
      .then((d) => live && setData(d))
      .catch((e: unknown) => {
        if (!live) return;
        if (e instanceof ApiError && (e.status === 401 || e.status === 503)) router.replace("/");
        else setError(e instanceof ApiError ? e.message : "Something went wrong.");
      });
    return () => {
      live = false;
    };
    // `load` is a fresh closure each render; `reload` is the explicit refetch signal
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload]);
  return { data, error };
}

const SAMPLES = [
  { name: "Harbor Gems", owed: 230000, oldest: 12 },
  { name: "Lakeside Fine Jewelry", owed: 596000, oldest: 31 },
];

export function SampleRows() {
  return (
    <>
      {SAMPLES.map((s) => (
        <tr key={s.name} className="text-muted">
          <td className="px-3 py-2">
            {s.name} <span className="text-xs">(sample — not on PayPal)</span>
          </td>
          <td className="px-3 py-2 text-right">…</td>
          <td className="px-3 py-2 text-right">…</td>
          <td className="px-3 py-2 text-right">…</td>
          <td className="tabular px-3 py-2 text-right">{formatCents(s.owed)}</td>
          <td className="tabular px-3 py-2 text-right">{s.oldest}d</td>
        </tr>
      ))}
    </>
  );
}

/** The same sample rows as stacked cards for phones (< 640 px, UI_UX §6). */
export function SampleCards() {
  return (
    <>
      {SAMPLES.map((s) => (
        <li key={s.name} className="px-4 py-3 text-sm text-muted">
          {s.name} <span className="text-xs">(sample — not on PayPal)</span>
          <p className="tabular">
            Owes {formatCents(s.owed)} · oldest {s.oldest}d
          </p>
        </li>
      ))}
    </>
  );
}
