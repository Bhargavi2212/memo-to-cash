# Research behind the PRD (sources)

## Memo is normal trade
- JA/JBT jeweler survey: 61% asset inventory, 39% memo inventory — https://www.jckonline.com/editorial-article/new-study-from-ja-and-jbt/
- Signet consignment inventory $601.5M, 31% of stock — https://rapaport.com/magazine-article/shifting-tides-in-the-us-jewelry-market/
- Independents still taking goods on memo — https://rapaport.com/analysis/independent-jewelers-are-driving-the-us-diamond-market/

## Late payment pain (small business)
- Intuit QuickBooks 2026: >half have invoices unpaid past 30 days, avg $17.7K — https://kvia.com/?p=1478806
- Xero Q2 2026: 29.3 days to get paid, ~9 days late — https://www.wsoctv.com/news/real-cost-late/PSLENA6EKMYNXN2WIUNCA7KDW4/
- PYMNTS Feb 2026: 60% avoid raising late payment — https://www.pymnts.com/wp-content/uploads/2026/02/PYMNTS-Intelligence-Money-Mobility-Tracker-February-2026.pdf

## Demand for one payment across many invoices
- QuickBooks: pay an entire statement at once — https://feedback.qbo.intuit.com/forums/920104-quickbooks-invoice-online-checkout/suggestions/39246715-ability-for-customer-to-pay-multiple-invoices-from
- QuickBooks: "90% of my customers want to do exactly that" — https://feedback.qbo.intuit.com/forums/591862-quickbooks-invoice-payments-portal/suggestions/19048006-ability-to-pay-remaining-outstanding-balance?page=4
- Square: batch invoicing request — https://community.squareup.com/t5/Archived-Ideas-Read-Only/Feature-Request-Merge-invoices-to-allow-customers-to-make-a/idc-p/221736/highlight/true
- Xero/Stripe: select and pay multiple invoices — https://productideas.xero.com/forums/939198-for-small-businesses/suggestions/46309624-stripe-option-to-select-and-pay-multiple-invoice

## Existing tools (what's not new)
- Versapay portal: multi-invoice, partial, disputes — https://www.versapay.com/ ; comparison https://monk.com/blog/best-b2b-customer-payment-portals
- Pricing not public (enterprise) — https://fintechspecs.com/blog/invoiced-vs-versapay-vs-billtrust-ar-automation/
- Jewelry ERP memo handling (Luxare by Diaspark) — https://www.luxare.com/resource/order-management-for-jewelry-wholesalers-finally-taming-consignment-memo-chaos

## PayPal platform facts
- Invoicing v2 (record payment methods, statuses, 2 reminders/day) — https://developer.paypal.com/docs/api/invoicing/v2/
- Webhook events (INVOICING.INVOICE.PAID etc.) — https://developer.paypal.com/api/rest/webhooks/event-names/
- Venmo (US/USD, app required, desktop QR) — https://developer.paypal.com/docs/checkout/pay-with-venmo/
- Pay Later US ranges ($30–$1,500 Pay in 4; $199–$10,000 Pay Monthly) — https://developer.paypal.com/limited-release/pay-later-commerce-platforms
- Fees: invoices paid by bank 1% capped $10; PayPal/Venmo 3.49% + fixed — https://www.paypalobjects.com/marketing/ua/pdf/US/us-merchant-fees-01-september-2026.pdf

## Our sandbox tests (Nov 8–9, 2026)
- Invoice NET_30 + PO ref, partial pay, send without email, buyer partial pay on PayPal page, deduction via PUT, cheque recorded, overpay refused, search by reference — all worked.
- One $1,500 checkout split across two invoices: A → MARKED_AS_PAID ($0 due), B → PARTIALLY_PAID ($500 due); B then paid on PayPal's page.
- `payment_id` = checkout capture ID on record-payment → RESOURCE_NOT_FOUND (PayPal won't link it).
- Pay in 4 / Pay Monthly offered automatically on a $1,500 checkout.

## Competitors in this hackathon (rechecked Nov 9)
- No entry with a buyer statement / multi-invoice payment (404 repos scanned + new repos since Nov 4).
- prateekraj91 reconciliation agent: strong matching, AI verdicts, dashboard (commits Nov 9). We don't compete on matching — statement payments arrive pre-allocated.
- Supplora Pay (AP for restaurants), Unstuck and CashFlow AI (collections) — no statements.
