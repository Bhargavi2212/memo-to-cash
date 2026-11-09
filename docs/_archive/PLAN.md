# Memo-to-Cash — Build plan (phases)

Deadline: **Thu Nov 12, 12:00pm PT (3:00pm ET)**. Target: submitted by **Wed Nov 11, 11:00pm ET**. Work phase by phase: spec → code → test → commit.

| Phase | When | Build | Done when (test) |
|---|---|---|---|
| P0 Setup | Mon | Next.js + FastAPI on Vercel, Neon DB, env vars, sandbox guard, CI running tests | `/api/health` live on Vercel; CI green |
| P1 Data + mirror | Mon | Schema + migrations, seed demo data, CSV import, mirror invoices to PayPal (flow A) | Import seed → 3 PayPal invoices exist with memo refs; re-import creates none |
| P2 Statement pay | Tue | Statement page, split function, order/capture, record per invoice (flow B) | Property tests on split; sandbox pay $5,000 → invoices show correct due |
| P3 Supplier + webhooks | Tue | Supplier screens, payment detail, webhook handler (flow E), adjustments (C), reminders (D) | Paying one invoice on PayPal's page updates the statement |
| P4 AI | Wed | Jobs 1–3, proposals, eval set + `make eval` | Eval table printed; unsafe-action rate 0 |
| P5 Ship | Wed | Judge path, reset demo, README (criteria → evidence), video, Devpost text, submit | Stage One checklist all ticked; submitted |

Cut list if late (in order): reminder drafting → webhook refunds/cancel → Pay Later/Venmo buttons (keep PayPal) → CSV upload UI (keep seed).
Never cut: statement payment, split + record on PayPal, Job 1/2 with eval, live link, video.
