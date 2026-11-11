// Record ids from the agent ("memo_line:S-102", "return:R-311", "invoice:INV-1042",
// "memo:M-207", "payment:1", "proposal:seed") → a chip label and the element to scroll to
// (P4 D14, G5). Labels come from the statement's own data, never from model text.
import type { Statement } from "./api";
import { daysAgo } from "./dates";
import { formatCents } from "./money";

export interface RecordChip {
  id: string;
  label: string;
  target: string | null; // DOM id on the statement page
}

export function recordChip(id: string, s: Statement): RecordChip {
  const [kind, ref = ""] = id.split(":", 2);
  const lines = s.memos.flatMap((m) => m.lines);
  switch (kind) {
    case "memo_line": {
      const line = lines.find((l) => l.sku.toUpperCase() === ref.toUpperCase());
      return {
        id,
        label: line ? `${line.sku} · ${line.description}` : ref,
        target: line ? `line-${line.sku}` : null,
      };
    }
    case "return": {
      const line = lines.find(
        (l) => l.return_ref?.toUpperCase() === ref.toUpperCase(),
      );
      const ago = line?.returned_on
        ? ` · ${daysAgo(line.returned_on)} days ago`
        : "";
      return {
        id,
        label: `Return ${ref}${ago}`,
        target: line ? `line-${line.sku}` : null,
      };
    }
    case "invoice":
      return { id, label: ref, target: `invoice-${ref}` };
    case "memo":
      return { id, label: `Memo ${ref}`, target: `memo-${ref}` };
    case "payment": {
      const p = s.payments.find((x) => String(x.id) === ref);
      return {
        id,
        label: p ? `Payment ${formatCents(p.amount_cents)}` : "Payment",
        target: null,
      };
    }
    case "proposal":
      return { id, label: "Your request", target: "requests" };
    default:
      return { id, label: ref || id, target: null };
  }
}
