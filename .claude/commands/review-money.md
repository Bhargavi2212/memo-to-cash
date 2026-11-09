Review the current diff for money and PayPal safety. Answer each with evidence (file:line):
1. Any float/Decimal-to-float used for money? (must be int cents)
2. Is every split going through backend/core/split.py? Property tests still cover the change?
3. Can a double click, retry or webhook replay create a second payment/allocation/record? Which unique key stops it?
4. Are DB money writes in one transaction, with PayPal calls after commit and their outcome stored?
5. Could the AI cause any money change without a supplier decision?
6. Are all queries scoped to the token's buyer / demo session?
7. Any PayPal call that skips PayPal-Request-Id, logs secrets, or sends payment_id on record-payment?
8. Does the change match docs/TRD.md and docs/BACKEND_SCHEMA.md? If docs need updating, say exactly what.
