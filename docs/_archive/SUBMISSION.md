# Submission checklist and story

## Stage One (pass/fail) — must all be true
- [ ] Public GitHub repo, MIT license visible in the About box
- [ ] Working build at a public Vercel URL, free and open until **Dec 15**
- [ ] Public YouTube video < 3:00 showing it working end to end, no copyrighted music/logos
- [ ] PayPal is central (Invoicing v2, Orders v2, JS SDK, webhooks); AI is meaningful (Llama: Q&A, triage, drafts)
- [ ] Tools section names Meta Llama API and how it's used
- [ ] Testing instructions: judge path + sandbox buyer login
- [ ] No client/real company names anywhere (repo, data, video)
- [ ] Actually **submitted**, not draft

## Video (≤ 3:00)
- 0:00–0:20 Pain: memo out 2 months → invoice arrives → buyer can't tell what they owe. On screen: QuickBooks/Square users asking for one payment for many invoices; "PayPal Invoicing doesn't have it."
- 0:20–1:20 Buyer: open statement (returned / kept / owed), ask "Why is S-102 here?", agent answers with the return record, pay $5,000 in one PayPal checkout.
- 1:20–2:10 Supplier: payment already split across PayPal invoices (open PayPal's invoice page), approve a deduction the agent proposed.
- 2:10–2:45 Proof: eval table vs. baseline; PayPal limit + our workaround (one invoice per page → statement payment; MARKED_AS_PAID explained).
- 2:45–3:00 Who it's for: memo/consignment traders first (39% of jeweler inventory on memo), any business with many invoices per customer next.

## README map (one section per judging criterion, with evidence)
Technological Implementation · Design · Potential Impact · Innovation (vs. Versapay/QuickBooks, what's new) · Presentation · Limits & workarounds · How to run · Tests & eval.
