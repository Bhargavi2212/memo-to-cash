"use client";
// Shared bits of the supplier pages (P3 A10, A12, G14): the thin banner back to a buyer's
// statement and the 401 → landing redirect. Every demo buyer is real (P3 §M) — no sample rows.
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";

/** Links to `token`'s statement (a buyer page: that buyer, P3 §M M4); without one, to the copy's
 * Northside statement (the overview). */
export function SupplierBanner({ token: forBuyer }: { token?: string } = {}) {
  const [copyToken, setCopyToken] = useState<string | null>(null);
  useEffect(() => {
    if (forBuyer) return;
    let live = true;
    api
      .whoami()
      .then((w) => live && setCopyToken(w.statement_token))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [forBuyer]);
  const token = forBuyer ?? copyToken;
  return (
    // two lines reserved on phones so the late link doesn't push the page down
    <p className="min-h-14 rounded-card bg-accent/5 px-4 py-2 text-sm sm:min-h-0">
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
