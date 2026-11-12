"use client";
// Import an ERP export (FR-1; P3b B7, G6, G7, X3, X4; UI_UX §2 `/supplier/import`). The admin
// token is a password field held in React state only — never stored, never in a URL. Judges
// have no token: this page is shown in the video (F7). Deliberately not `useSupplierData`: it
// needs no cookie and never redirects (X4).
import { useState } from "react";

import { ApiError, api, type ImportResult } from "@/lib/api";
import { useHydrated } from "@/lib/useHydrated";

const SETUP: Record<string, string> = {
  READY: "on PayPal",
  SETTING_UP: "setting up on PayPal…",
  FAILED: "couldn't set up on PayPal — open the statement to retry",
};

export function ImportPage() {
  const ready = useHydrated();
  const [token, setToken] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);

  const submit = () => {
    if (!token.trim()) return setError("Enter the admin token.");
    if (!file) return setError("Choose a CSV file.");
    setBusy(true);
    setError(null);
    setResult(null);
    api.supplier
      .importCsv(token.trim(), file)
      .then(setResult)
      .catch((e: unknown) => {
        if (e instanceof ApiError && (e.status === 401 || e.status === 404))
          return setError("That admin token isn't valid.");
        setError(e instanceof ApiError ? e.message : "Something went wrong. Please try again.");
      })
      .finally(() => setBusy(false));
  };

  const count = (r: Record<string, number>) =>
    Object.entries(r)
      .filter(([, n]) => n > 0)
      .map(([k, n]) => `${n} ${k}`)
      .join(" · ") || "nothing";

  return (
    <div className="min-w-0 max-w-2xl space-y-6 break-words">
      <header>
        <h1 className="text-xl font-semibold">Import an ERP export</h1>
        <p className="mt-1 text-sm text-muted">
          One CSV with buyers, memos, lines, returns and invoices. Every row is checked first — a
          file with any bad row changes nothing. New invoices go on PayPal right away.{" "}
          <a href="/api/supplier/import/sample.csv" download="sample_export.csv" className="text-accent underline">
            Download the sample file
          </a>{" "}
          (three buyers, dated today).
        </p>
      </header>

      <div className="space-y-3 rounded-card border border-line bg-surface p-4 text-sm">
        <div>
          <label htmlFor="imp-token" className="block">
            Admin token
          </label>
          <input
            id="imp-token"
            type="password"
            autoComplete="off"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            className="mt-1 block min-h-11 w-full rounded-card border border-line px-3"
          />
        </div>
        <div>
          <label htmlFor="imp-file" className="block">
            CSV file (up to 1 MB)
          </label>
          <input
            id="imp-file"
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="mt-1 block w-full text-sm"
          />
        </div>
        {error && (
          <p role="alert" className="text-danger">
            {error}
          </p>
        )}
        <button
          type="button"
          onClick={submit}
          disabled={busy || !ready}
          className="min-h-11 rounded-card bg-accent px-4 font-medium text-white disabled:opacity-50"
        >
          {busy ? "Importing… (up to 20 s)" : "Import"}
        </button>
      </div>

      {result && (
        <section aria-labelledby="imp-res" aria-live="polite" className="space-y-3 rounded-card border border-line bg-surface p-4 text-sm">
          <h2 id="imp-res" className="text-lg font-semibold">
            {result.errors.length ? "Nothing imported" : "Imported"}
          </h2>
          {result.errors.length > 0 ? (
            <ul className="space-y-1 text-danger">
              {result.errors.map((e, i) => (
                <li key={i}>
                  Row {e.row}: {e.msg}
                </li>
              ))}
            </ul>
          ) : (
            <>
              <p>
                Created {count(result.created)}; updated {count(result.updated)}.{" "}
                {result.mirrored} invoice{result.mirrored === 1 ? "" : "s"} put on PayPal
                {result.setting_up > 0 && `, ${result.setting_up} still setting up`}.
              </p>
              {result.mirror_errors.length > 0 && (
                <ul className="space-y-1 text-warning">
                  {result.mirror_errors.map((e) => (
                    <li key={e.invoice_no}>
                      {e.invoice_no}: {e.msg}
                    </li>
                  ))}
                </ul>
              )}
              <ul className="divide-y divide-line">
                {result.buyers.map((b) => (
                  <li key={b.id} className="py-2">
                    <span className="font-medium">{b.name}</span>{" "}
                    <span className="text-muted">· {SETUP[b.paypal_setup] ?? b.paypal_setup}</span>
                    <a href={b.statement_url} className="ml-2 text-accent underline">
                      Statement link
                    </a>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}
    </div>
  );
}
