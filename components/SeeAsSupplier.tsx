"use client";
// "See it as Demo Diamond Co. →" (P3 A10, J1 step 6): shown only when the visitor's demo cookie
// owns THIS statement (`/demo/whoami` returns the same token), so a shared link never opens
// someone else's copy as the supplier (R10).
import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";

export function SeeAsSupplier({ token }: { token: string }) {
  const [mine, setMine] = useState(false);
  useEffect(() => {
    let live = true;
    api
      .whoami()
      .then((w) => live && setMine(w.statement_token === token))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [token]);
  if (!mine) return null;
  return (
    <Link
      href="/supplier"
      className="inline-flex min-h-11 items-center rounded-card border border-accent px-4 text-sm font-medium text-accent"
    >
      See it as Demo Diamond Co. →
    </Link>
  );
}
