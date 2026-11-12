"use client";
// "See it as Demo Diamond Co. →" (P3 A10, J1 step 6): shown only when the visitor's demo cookie
// owns THIS statement (its token is one of `/demo/whoami`'s buyers — any of the copy's three,
// P3 §M G4), so a shared link never opens someone else's copy as the supplier (R10). Northside
// links to the overview (J1 step 6); Harbor / Lakeside to their own supplier page.
import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";

export function SeeAsSupplier({ token }: { token: string }) {
  const [href, setHref] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api
      .whoami()
      .then((w) => {
        if (!live) return;
        const b = w.buyers.find((x) => x.statement_token === token);
        // Northside → the overview (J1 step 6, the judged path); the others → their own page
        setHref(!b ? null : token === w.statement_token ? "/supplier" : `/supplier/buyers/${b.id}`);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [token]);
  if (href === null) return null;
  return (
    <Link
      href={href}
      className="inline-flex min-h-11 items-center rounded-card border border-accent px-4 text-sm font-medium text-accent"
    >
      See it as Demo Diamond Co. →
    </Link>
  );
}
