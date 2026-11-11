"use client";
// Invoices with checkboxes (open ones only) and status chips (P2 plan G3): every invoice is
// listed; paid ones show "Paid" and can't be selected.
import type { Invoice } from "@/lib/api";
import { daysUntil } from "@/lib/dates";
import { formatCents } from "@/lib/money";

import { StatusChip } from "./StatusChip";

function Chip({ inv }: { inv: Invoice }) {
  if (inv.due_cents === 0) return <StatusChip tone="success">Paid</StatusChip>;
  if (inv.days_overdue > 0)
    return <StatusChip tone="warning">Overdue {inv.days_overdue}d</StatusChip>;
  const chip = inv.paid_cents > 0 ? <StatusChip tone="accent">Part paid</StatusChip> : null;
  const days = daysUntil(inv.due_on);
  return (
    <span className="flex flex-wrap gap-1">
      {chip}
      <StatusChip tone="muted">{days === 0 ? "Due today" : `Due in ${days}d`}</StatusChip>
    </span>
  );
}

export function InvoiceList({
  invoices,
  selected,
  onToggle,
  disabled,
  focus = null,
}: {
  invoices: Invoice[];
  selected: Set<number>;
  onToggle: (id: number) => void;
  disabled: boolean;
  focus?: string | null; // "invoice-INV-1042" from a record chip (P4 G5)
}) {
  return (
    <ul className="divide-y divide-line rounded-card border border-line bg-surface">
      {invoices.map((inv) => {
        const open = inv.due_cents > 0;
        return (
          <li
            key={inv.id}
            id={`invoice-${inv.invoice_no}`}
            className={`transition-colors ${focus === `invoice-${inv.invoice_no}` ? "bg-accent/10" : ""}`}
          >
            <label
              className={`flex min-h-11 items-start gap-3 px-4 py-3 ${open ? "cursor-pointer" : "opacity-70"}`}
            >
              <input
                type="checkbox"
                className="mt-1 h-5 w-5 accent-accent"
                checked={open && selected.has(inv.id)}
                disabled={!open || disabled}
                onChange={() => onToggle(inv.id)}
                aria-label={`Pay ${inv.invoice_no}`}
              />
              <span className="flex-1">
                <span className="flex items-baseline justify-between gap-3">
                  <span className="font-medium">{inv.invoice_no}</span>
                  <span className="tabular text-right">
                    {formatCents(inv.due_cents)}
                    {inv.paid_cents > 0 && inv.due_cents > 0 && (
                      <span className="block text-xs text-muted">
                        of {formatCents(inv.total_cents)}
                      </span>
                    )}
                  </span>
                </span>
                <span className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted">
                  <span>Memo {inv.memo_no}</span>
                  <Chip inv={inv} />
                  {inv.paypal_url && (
                    <a
                      href={inv.paypal_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-accent underline"
                    >
                      View on PayPal
                    </a>
                  )}
                </span>
              </span>
            </label>
          </li>
        );
      })}
    </ul>
  );
}
