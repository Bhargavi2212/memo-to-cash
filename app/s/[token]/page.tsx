import { Suspense } from "react";

import { Statement, StatementSkeleton } from "@/components/Statement";

// Server shell; the client component reads the token with useParams() inside <Suspense>
// (P2 R6: reading params outside Suspense fails `next build` with Cache Components).
export default function StatementPage() {
  return (
    <Suspense fallback={<StatementSkeleton />}>
      <Statement />
    </Suspense>
  );
}
