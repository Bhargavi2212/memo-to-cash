import { Suspense } from "react";

import { PaymentDetail } from "@/components/supplier/PaymentDetail";

// useParams() is read inside <Suspense> (P2 R6, Cache Components).
export default function SupplierPaymentPage() {
  return (
    <Suspense fallback={<div aria-busy="true" className="h-48 animate-pulse rounded-card bg-line" />}>
      <PaymentDetail />
    </Suspense>
  );
}
