import { Suspense } from "react";

import { BuyerDetail } from "@/components/supplier/BuyerDetail";

// useParams() is read inside <Suspense> (P2 R6, Cache Components).
export default function SupplierBuyerPage() {
  return (
    <Suspense fallback={<div aria-busy="true" className="h-48 animate-pulse rounded-card bg-line" />}>
      <BuyerDetail />
    </Suspense>
  );
}
