import { Suspense } from "react";

import { Overview } from "@/components/supplier/Overview";

// Server shell; data comes from the client with the visitor's demo cookie (P3 A1, G14).
export default function SupplierPage() {
  return (
    <Suspense fallback={<div aria-busy="true" className="h-48 animate-pulse rounded-card bg-line" />}>
      <Overview />
    </Suspense>
  );
}
