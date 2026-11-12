// node --test (Node 22 strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";

import { paymentMethodLabel } from "./labels.ts";

test("the two PayPal sources read differently (P3b G15)", () => {
  assert.equal(paymentMethodLabel("STATEMENT", null), "PayPal");
  assert.equal(paymentMethodLabel("PAYPAL_INVOICE_PAGE", "PAYPAL"), "Paid on PayPal's invoice page");
  assert.equal(paymentMethodLabel("OFF_PAYPAL", "CHECK"), "Cheque");
  assert.equal(paymentMethodLabel("OFF_PAYPAL", null), "Other");
});
