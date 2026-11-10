import { Suspense } from "react";

import { Receipt, ReceiptSkeleton } from "@/components/Receipt";

// Server shell; the client component reads the params with useParams() inside <Suspense> (R6).
export default function ReceiptPage() {
  return (
    <Suspense fallback={<ReceiptSkeleton />}>
      <Receipt />
    </Suspense>
  );
}
