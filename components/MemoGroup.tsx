"use client";
// One memo and its lines: Kept → INV, Returned · R-311, Still with you (UI_UX §4.1,
// judged moment 1). Collapsible; the first memo starts open.
import { useState } from "react";

import type { Line, Memo } from "@/lib/api";
import { daysAgo, daysUntil } from "@/lib/dates";
import { formatCents } from "@/lib/money";

import { StatusChip } from "./StatusChip";

function LineStatus({ line }: { line: Line }) {
  if (line.status === "KEPT")
    return <StatusChip tone="accent">Kept → {line.invoice_no ?? "invoice"}</StatusChip>;
  if (line.status === "RETURNED") {
    const ago = line.returned_on ? `, ${daysAgo(line.returned_on)}d ago` : "";
    return (
      <StatusChip tone="success">
        ✓ Returned{line.return_ref ? ` · ${line.return_ref}` : ""}
        {ago}
      </StatusChip>
    );
  }
  const due = line.due_back_on ? daysUntil(line.due_back_on) : null;
  const when =
    due === null ? "" : due >= 0 ? `, due back in ${due}d` : `, due back ${-due}d ago`;
  return <StatusChip tone="muted">Still with you{when}</StatusChip>;
}

export function MemoGroup({
  memo,
  defaultOpen,
  focus = null,
}: {
  memo: Memo;
  defaultOpen: boolean;
  focus?: string | null; // a record chip's target: "line-S-102" or "memo-M-207" (P4 D14)
}) {
  const [userOpen, setOpen] = useState(defaultOpen);
  const focusSku = focus?.startsWith("line-") ? focus.slice(5) : null;
  const memoFocused = focus === `memo-${memo.memo_no}`;
  // a chip pointing into this memo opens it (derived, so no effect is needed)
  const open = userOpen || memoFocused || memo.lines.some((l) => l.sku === focusSku);
  const kept = memo.lines.filter((l) => l.status === "KEPT").length;
  const returned = memo.lines.filter((l) => l.status === "RETURNED").length;
  const out = memo.lines.length - kept - returned;
  return (
    <section
      id={`memo-${memo.memo_no}`}
      className={`rounded-card border border-line bg-surface transition-shadow ${memoFocused ? "ring-2 ring-accent" : ""}`}
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex min-h-11 w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <span>
          <span className="font-medium">Memo {memo.memo_no}</span>
          <span className="ml-2 text-sm text-muted">sent {daysAgo(memo.sent_on)}d ago</span>
        </span>
        <span className="text-xs text-muted">
          {kept} kept · {returned} returned · {out} with you {open ? "▾" : "▸"}
        </span>
      </button>
      {open && (
        <ul className="divide-y divide-line border-t border-line">
          {memo.lines.map((line) => (
            <li
              key={line.sku}
              id={`line-${line.sku}`}
              className={`px-4 py-3 transition-colors ${focusSku === line.sku ? "bg-accent/10" : ""}`}
            >
              <div className="flex items-baseline justify-between gap-3">
                <span>
                  <span className="font-medium">{line.sku}</span>{" "}
                  <span className="text-muted">{line.description}</span>
                </span>
                <span className="tabular text-right">{formatCents(line.price_cents)}</span>
              </div>
              <div className="mt-1">
                <LineStatus line={line} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
