// "Your requests" (P4 D17): what the buyer sent and where it stands, newest first. Summaries
// are written by code from the request, never the raw model text.
import type { BuyerRequest, RequestStatus } from "@/lib/api";
import { shortDate } from "@/lib/dates";

import { StatusChip } from "./StatusChip";

const STATUS: Record<
  RequestStatus,
  { tone: "muted" | "success" | "danger" | "accent"; text: string }
> = {
  WAITING: { tone: "muted", text: "Waiting for Demo Diamond Co." },
  APPROVED: { tone: "success", text: "Approved" },
  DECLINED: { tone: "danger", text: "Declined" },
  ANSWERED: { tone: "accent", text: "Answered" },
};

export function YourRequests({
  requests,
  highlighted,
}: {
  requests: BuyerRequest[];
  highlighted: boolean;
}) {
  if (requests.length === 0) return null;
  return (
    <section
      id="requests"
      aria-labelledby="req-h"
      className={`space-y-3 rounded-card transition-shadow ${highlighted ? "ring-2 ring-accent" : ""}`}
    >
      <h2 id="req-h" className="text-lg font-semibold">
        Your requests
      </h2>
      <ul className="divide-y divide-line rounded-card border border-line bg-surface">
        {requests.map((r) => (
          <li key={r.id} className="px-4 py-3">
            <p>{r.summary}</p>
            <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted">
              <StatusChip tone={STATUS[r.status].tone}>
                {STATUS[r.status].text}
              </StatusChip>
              {r.created_at && <span>{shortDate(r.created_at)}</span>}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}
