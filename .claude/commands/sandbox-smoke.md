Run the end-to-end check against the real PayPal sandbox: `python -m scripts.sandbox_smoke`.
It must: create a demo buyer, mirror invoices, create one order for a multi-invoice split, pause for the user to approve with the SANDBOX PERSONAL buyer (never a real account), capture, record per invoice, then GET each invoice and assert PayPal due amounts equal our ledger.
Report each step with PayPal ids and debug_ids on failure. Never print secrets.
