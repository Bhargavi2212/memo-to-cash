// Banner above the total for recent payment events (UI_UX §4.1 "Notices", P2 D2).
import type { Notice } from "@/lib/api";
import { formatCents } from "@/lib/money";

function text(n: Notice): string {
  switch (n.kind) {
    case "PAYMENT_VOIDED":
      return "A payment you started didn't go through — nothing was charged.";
    case "PAYMENT_CREDITED":
      return `${formatCents(n.amount_cents ?? 0)} of your payment is kept as credit on your account.`;
    case "RECORDING_PENDING":
      return "Your payment is received; we're updating your invoices.";
  }
}

export function Notices({ notices }: { notices: Notice[] }) {
  if (!notices.length) return null;
  return (
    <div role="status" className="space-y-2">
      {notices.map((n) => (
        <p
          key={`${n.kind}-${n.payment_id}`}
          className="rounded-card border border-line bg-surface px-4 py-3 text-sm"
        >
          {text(n)}
        </p>
      ))}
    </div>
  );
}
