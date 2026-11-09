# Memo-to-Cash — Product Requirements

Status: draft v1 (Nov 9, 2026). Owner: Bhargavi. Every claim has a source in `RESEARCH.md`.

## 1. The problem, in one scene
A diamond/jewelry supplier sends goods to a retailer **on memo** (consignment). The goods sit with the retailer for **1–3 months**. The retailer keeps some pieces and returns the rest. The supplier's staff process the return in their ERP and invoice the kept pieces. The invoice arrives by email, often weeks later.

Now the retailer has to work out, by hand: *which memo is this invoice for, did they credit what I sent back, how much do I owe across all these invoices?* Only then do they write a cheque or send Venmo. Meanwhile the supplier's accountant chases them by email and phone, then matches whatever money arrives back to invoices.

Money is slow not because buyers won't pay, but because **the bill doesn't explain itself and can't be paid in one go.**

## 2. Why this matters (evidence)
- Memo is normal trade, not an edge case: in a JA/JBT survey, **39% of US jewelers' store merchandise was memo inventory**. Signet held **$601.5M** of consignment stock (31% of inventory).
- Small businesses wait: **more than half** have invoices unpaid past 30 days, averaging **$17.7K** (Intuit 2026). Payments arrive **~9 days late** on average (Xero, Q2 2026). **60%** avoid raising it for fear of the relationship (PYMNTS).
- Buyers want one payment for many invoices. QuickBooks users: *"90% of my customers want to do exactly that"*; Square and Xero/Stripe users ask for the same.
- Big companies already get this from Versapay, Billtrust, Upflow and Invoiced (statement portals, multi-invoice pay, disputes) — sold on custom enterprise contracts.
- **PayPal Invoicing has no statement or multi-invoice payment.** Payments are recorded per invoice.

## 3. Who it's for
- **Supplier** (primary customer): small/mid wholesaler (diamonds, jewelry first; any memo/consignment trade later). Already has an ERP and a PayPal business account. Roles: owner, accountant.
- **Buyer**: retailer receiving goods on memo. Pays by cheque, Venmo, PayPal, bank today. Uses phone and desktop.

## 4. What we build (scope v1)
1. **Mirror ERP invoices as real PayPal invoices** — each kept memo line becomes a line item; each invoice keeps its memo reference. Partial payment allowed.
2. **Buyer statement link** — one page per buyer: every open memo, which lines were returned (credited), which were kept (invoiced), what's owed, how old.
3. **Pay many invoices in one payment** — buyer picks invoices or an amount; one PayPal checkout (PayPal, Pay Later, Venmo button, bank, card); we split the payment across the PayPal invoices and record it on each.
4. **"Ask about this statement"** — an AI agent that answers only from the memo/return/invoice records ("Why is S-102 here? I returned it.").
5. **Buyer messages → proposed actions** — the agent turns a message into a structured proposal (return claim, deduction request, payment promise, dispute). **Nothing changes until the supplier approves.**
6. **Supplier view** — who owes what, payments already split, agent proposals to approve, reminder drafts (sent via PayPal's own invoice reminder).
7. **Stay in sync with PayPal** — webhooks update the statement when a buyer pays a single invoice directly on PayPal's page.
8. **Record money that didn't come through PayPal** — the supplier records a cheque/wire/other payment against one or more invoices; it is split with the same rule and recorded on each PayPal invoice (`method: CHECK` / `WIRE_TRANSFER` / `OTHER`), so the statement stays complete.
9. **Getting the link to the buyer** — v1: the supplier copies the statement link from the dashboard, and the link is also written into each PayPal invoice's note so PayPal's own invoice email carries it. (No email sending of our own in v1.)

## 5. Not in v1 (deliberately)
- A direct ERP connector (we import a CSV export; adapters come later).
- Inventory/memo tracking itself (the ERP and jewelry systems already do it).
- WhatsApp/voice intake (adds steps for users — rejected).
- Pitching Pay Later as B2B financing (it is a consumer credit product; we only show it is offered).
- Auto-sending anything or changing any amount without supplier approval.
- Refunds/reversals of statement payments (handled in PayPal manually in v1; we only show them).
- Tax, shipping and multi-currency (USD only; invoice amount = sum of kept line prices ± approved adjustments).

## 6. Success measures
- Product: days from invoice to paid; % of payments arriving already allocated (target 100% via statement page); accountant minutes per payment; buyer questions resolved without a call.
- AI: accuracy on the labelled message set vs. a keyword baseline (see `TRD.md` §6); zero money changes without approval; zero answers that cite a record that doesn't exist.

## 7. Known platform limits (stated up front in the submission)
- PayPal's invoice page pays one invoice at a time → we built the statement payment.
- Payments we record show `MARKED_AS_PAID` / `PARTIALLY_PAID`, not `PAID`: PayPal only links payments made on its own invoice page (our test: `payment_id` from a checkout capture is rejected with `RESOURCE_NOT_FOUND`). We store the capture ID on the payment and put the allocation token `[MTC:<uid>]` plus the capture ID in each recorded payment's note on PayPal, for traceability.
- Invoice reminders: max 2 per day per PayPal. Our chasing respects it.
- Venmo: US/USD, needs the Venmo app; desktop flow is a QR code. Judges can see the button; sandbox payments in the demo use PayPal/bank.

## 8. Assumptions (confirm with real documents)
- The ERP can export memos, memo lines, returns and invoices (CSV or report) — format in `BACKEND_SCHEMA.md §6`.
- One ERP invoice covers kept lines from one memo; invoice total = sum of those lines' memo prices.
- Buyers are US retailers paying in USD.
- Demo data is fictional; no real client, buyer or ERP names anywhere.
