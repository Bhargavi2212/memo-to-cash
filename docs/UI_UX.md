# Memo-to-Cash — UI / UX

## 1. Principles
1. **The bill explains itself.** Every number on screen can be traced to a memo line, a return, or a payment in one tap.
2. **One clear action per screen.** Buyer: pay. Supplier: decide.
3. **Nothing changes money without a person.** AI suggestions look like suggestions (dashed border, "Suggested by agent").
4. **Judges understand it in 10 seconds** with no login and no setup.
5. Mobile-first for buyers; desktop-first for suppliers.

## 2. Routes
| Route | Who | Purpose |
|---|---|---|
| `/` | Judge/visitor | One-line pain, **Start the demo** (creates the visitor's own copy), **See the supplier side**, sandbox note |
| `/s/[token]` | Buyer | Statement, pay, ask |
| `/s/[token]/receipt/[paymentId]` | Buyer | Receipt after paying |
| `/supplier` | Supplier | Who owes what + decisions panel |
| `/supplier/buyers/[id]` | Supplier | One buyer's memos, invoices, messages, history |
| `/supplier/payments/[id]` | Supplier | Payment split + PayPal status + audit |
| `/supplier/import` | Supplier | Upload ERP export |

## 3. Design tokens
- Font: Inter; numbers `font-variant-numeric: tabular-nums`.
- Colours: ink `#111827`, muted `#6B7280`, line `#E5E7EB`, background `#FFFFFF`/`#F9FAFB`, accent `#1D4ED8` (actions), success `#047857`, warning `#B45309`, danger `#B91C1C`. PayPal buttons keep PayPal's own styling.
- Status chips (text + colour): Returned (green), Kept (blue), Still with you (grey), Overdue 18d (amber, computed), Paid (green), Part paid (blue).
- Radius 8px, spacing 4/8/16/24, max content width 960px (buyer), 1280px (supplier).

## 4. Screens
### 4.1 Buyer statement `/s/[token]` (mobile)
```
┌───────────────────────────────┐
│ Demo Diamond Co.              │
│ Statement for Northside Jewel.│
│  You owe   $8,420.00          │
│  3 invoices · oldest 48 days  │
├───────────────────────────────┤
│ ▸ Memo M-201  sent 75d ago    │
│   S-101 Round 1.01ct  $2,350  │
│        Kept → INV-1038        │
│   S-102 Oval 1.20ct   $1,200  │
│        ✓ Returned · R-311     │
│   S-103 Pendant         $800  │
│        Still with you, due in 11d│
│   S-104 Stud pair     $1,150  │
│        Kept → INV-1038        │
│ ▸ Memo M-207 … ▸ Memo M-212 … │
├───────────────────────────────┤
│ Invoices                      │
│ ☑ INV-1038 $3,500 overdue 18d⚠│
│ ☑ INV-1042 $3,500 due in 10d  │
│ ☑ INV-1051 $1,420 due in 25d  │
│ Pay $8,420.00 → split shown   │
│ [ PayPal ] [ Pay Later ]      │
│ [ Venmo ]                     │
├───────────────────────────────┤
│ Ask about this statement      │
│ [Why is S-102 on my bill?]    │
│ [What do I owe for M-207?]    │
│ [ type a question…       ➤ ]  │
└───────────────────────────────┘
```
- Split preview appears under the amount. Default (all invoices): "INV-1038 $3,500 · INV-1042 $3,500 · INV-1051 $1,420". Demo story uses "Pay a different amount" = $5,000 → "INV-1038 $3,500 · INV-1042 $1,500".
- "Pay a different amount" opens a number field; preview updates live (debounced call to `/preview`).
- Demo mode only: "Pay a different amount" is **prefilled with $5,000** with the line "Demo: one payment split across two invoices — or pay the full $8,420" (P2 D20/R10), and a box next to the PayPal buttons shows the sandbox buyer login (email / password, "sandbox only") plus the guest-card fallback (P2 R9).
- The split is sent to PayPal as order items (INV-1038 $3,500 · INV-1042 $1,500, P2 D9), but PayPal's current checkout page shows only the total — so our own split preview (before) and receipt (after) carry the story; the video never claims the split shows inside PayPal's window.
- Answers show record chips (`Return R-311 · 38 days ago`) that scroll to the line.
- "Something wrong? Tell Demo Diamond Co." → free text → message (becomes a proposal).
- States: loading skeleton; all paid ("Nothing owed — thank you"); invalid link; PayPal error banner ("Payment didn't go through. You weren't charged.").
- Paid on PayPal's own invoice page (P3b F3/F4, G15): coming back to the tab refetches the statement (not during a payment); Pay on an invoice PayPal shows paid → banner "INV-1038 was paid on PayPal — your statement is updated." and the statement reloads, nothing charged. The payment then shows in the payments list as paid on PayPal's invoice page.
- Not yet on PayPal (P3 §M M9 — Harbor Gems / Lakeside Fine Jewelry for ~2 s after "Start the demo"): invoices shown; in place of the pay panel a note "Setting up on PayPal… Pay opens as soon as these invoices are on PayPal (a few seconds)." and a disabled **Pay with PayPal** button. Opening the page runs or joins the setup (waits ≤ 10 s) then reloads; still not ready → "Couldn't set up these invoices on PayPal." + **Retry** (refetch only on Retry).
- **Notices** (from `Statement.notices`, shown as a banner above the total): `PAYMENT_VOIDED` → "A payment you started didn't go through — nothing was charged." · `PAYMENT_CREDITED` → "$X of your payment is kept as credit on your account." · `RECORDING_PENDING` → "Your payment is received; we're updating your invoices." Credit (if any) shows under the total as "Credit $X".

### 4.2 Receipt
Amount, date, PayPal capture ID, list "INV-1038 $3,500 → paid in full", "INV-1042 $1,500 → $2,000 left", new balance, "View invoice on PayPal" links.

### 4.3 Supplier overview `/supplier` (desktop)
```
┌──────────────────────────────────────────────┬───────────────────────────┐
│ Open $16,680  Past due $7,000   Paid this week $0 │ Needs your decision (1) │
├──────────────────────────────────────────────┤ ┌───────────────────────┐ │
│ Buyer          Out  Kept  Ret.  Owed   Oldest │ │ Northside: "scratch on│ │
│ Northside Jew.  2    5    5   $8,420   48d ⚠ │ │ the S-120 solitaire,  │ │
│ Lakeside F. J.  1    4    1   $5,960   31d ⚠ │ │ take $20 off"         │ │
│ Harbor Gems     1    2    2   $2,300   12d   │ │ Checked: S-120 kept on│ │
├──────────────────────────────────────────────┤ │ INV-1051, price $1,420│ │
│ Recent payments                               │ │ → −$20 on INV-1051    │ │
│ <date>  Northside  $5,000  split 2 invoices ✓│ │ [Approve][Edit][Reject]│ │
└──────────────────────────────────────────────┴───────────────────────────┘
```
Demo mode: the overview shows the visitor's own copy — **three real buyers** (Northside Jewelers, Lakeside Fine Jewelry, Harbor Gems; P3 §M), each with PayPal invoices, a buyer page and its own statement link. No sample rows. Buyers are ordered by owed, largest first, then name. Header uses **"Past due"** (any overdue), not ">30d".

Not yet on PayPal (P3 §M M9): the buyer row shows "Setting up on PayPal…" and still opens; the overview refetches every 2 s while any row is setting up, for at most 30 s, then those rows read "Couldn't set up on PayPal — open to retry" (danger colour).

### 4.3c Import `/supplier/import` (P3b B7, X3, X4)
Heading "Import an ERP export"; one line on what happens + **Download the sample file** (three buyers, dated today). Fields: **Admin token** (password field, kept in memory only, never stored) and **CSV file (up to 1 MB)**; **Import** ("Importing… (up to 20 s)"). Result: "Imported" — created/updated counts, "N invoices put on PayPal, M still setting up", PayPal errors by invoice, then each buyer with its setup state and **Statement link**; or "Nothing imported" with "Row 4: …" per error. Wrong token → "That admin token isn't valid." Loads without a demo cookie (no redirect). The video shows it; the token on screen is rotated after recording.

### 4.3b Supplier buyer detail `/supplier/buyers/[id]`
Memos and lines (same grouping as the buyer sees), invoices with PayPal status, messages and proposals, payments. Buttons: **Copy statement link** · **Record payment** (modal: method Cheque/Wire/Other, reference, amount, optional invoices, split preview, Confirm). Not yet on PayPal (M9): **Record payment** disabled + the same setup note / Retry as the statement. Empty lists read "No payments yet." / "No messages yet." Past-due invoices without an active promise show **Draft a reminder** (P3b B6) → "Drafting…" → a REMINDER card in "Needs your decision" (the row then reads "Reminder waiting for your decision"); a failed draft: "Couldn't draft a reminder — try again." The REMINDER card shows the draft in an editable box ("Reminder PayPal will email"), the suggested send date, and **Send reminder on PayPal**; done → "Reminder sent on PayPal for INV-1038 · View on PayPal"; a 3rd the same day → "PayPal allows 2 reminders a day for an invoice — try again tomorrow."

### 4.4 Payment detail
Header: $5,000 · PayPal capture `4CN0…` · <date>. Table: invoice → amount → PayPal status (fetched) → link. Timeline: captured → split → recorded INV-1038 → recorded INV-1042 → webhook received.

### 4.5 Landing `/`
"Your buyers get one link: what they kept, what they returned, what they owe — and one button to pay it all on PayPal." Buttons: **Start the demo** (your own copy, ~5 s while PayPal invoices are created; show progress) · **See the supplier side** · **Start again** (always a brand-new copy; the old one is left behind). If PayPal's sandbox is slow, show the 503 message with a Retry button. Small print: "Sandbox only. Buyer login: … (sandbox)."

## 5. Copy rules
Plain words: "You owe", "Returned", "Kept", "Still with you". No jargon (no "AR", "allocation", "remittance" on buyer screens). Money always with 2 decimals and $.

## 6. Accessibility & responsive
Contrast AA; focus rings; chips include text; tables become stacked cards < 640px; buttons ≥ 44px tall; numbers right-aligned.

## 7. Video-critical moments (design must make these obvious)
1. Returned vs kept vs still-with-you on one screen.
2. Agent answer with a record chip.
3. One payment → split preview → receipt.
4. PayPal's own invoice page showing the new balance.
5. Supplier approving an agent proposal.
