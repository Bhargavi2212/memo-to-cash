# Memo-to-Cash — Screens and flows

Rule for every screen: a judge who opens the link with no login must understand it in 10 seconds. Demo data is fictional ("Demo Diamond Co." supplier, "Northside Jewelers" buyer). No real client names anywhere.

## Screen 1 — Supplier: "Who owes us what" (`/supplier`)
- Header: total open, total overdue > 30 days, payments received this week.
- Table, one row per buyer: open memos · lines out / kept / returned · amount owed · oldest item age · last payment · status chip (On track / Overdue / Needs your decision).
- Right panel **"Needs your decision"** (agent proposals), each card: what the buyer said (quoted), what the agent checked (records), proposed action, **Approve / Edit / Reject**. Examples:
  - "Buyer says S-102 was returned. No return record found. → Reply asking for the return slip" 
  - "Buyer asks $20 off S-104 (chip). Line price $1,150. → Add -$20 adjustment to INV-1042"
  - "INV-1038 is 63 days old, 2 reminders sent. → Send PayPal reminder (draft below)"
- States: empty (no buyers → "Import ERP export" button), loading, error (PayPal unreachable → banner, nothing lost).

## Screen 2 — Buyer statement (`/s/{token}`) — the link in the email
- Mobile-first. Top: supplier name, "You owe $8,420.00 across 3 invoices", oldest 63 days.
- Grouped **by memo**, each memo expandable:
  - Line rows: SKU · description · price · status: **Returned (credited Oct 2)** / **Kept → INV-1042** / **Still with you (due back Nov 20)**.
  - Invoice rows: invoice no. · total · paid · due · "View on PayPal" (opens PayPal's own invoice page).
- **Pay** panel: checkboxes per invoice (default: all, oldest first) or "Pay a different amount"; shows how the amount will be split before paying; PayPal / Pay Later / Venmo buttons (PayPal JS SDK).
- **Ask about this statement** box with 3 suggested questions. Answers cite the records ("Return received Oct 2, ref R-311"). If the agent finds a mismatch it offers "Send this to Demo Diamond Co." → becomes a proposal on Screen 1.
- After payment: receipt card — amount, PayPal capture ID, split per invoice, new balance.
- States: fully paid ("Nothing owed — thank you"), payment failed (PayPal error shown plainly, nothing recorded), link expired/invalid.

## Screen 3 — Supplier: payment detail (`/supplier/payments/{id}`)
- The payment, its PayPal capture ID, and the split: invoice → amount → PayPal status after recording (link to the PayPal invoice).
- Audit trail: every step with time (captured, allocated, recorded on PayPal invoice X, webhook received).

## Judge path (top of README and on the landing page `/`)
1. "Open Northside Jewelers' statement" → Screen 2 with seeded data.
2. "Pay with the sandbox buyer" (credentials shown on the page, sandbox only).
3. "See it from the supplier side" → Screen 1 and 3, approve one proposal.
4. "Reset demo" button (re-seeds; rate-limited).

## Visual style
Calm ledger look: white, one accent colour, tabular numbers, big clear totals, status chips with text (not colour only). Fast on phone. No stock illustrations.
