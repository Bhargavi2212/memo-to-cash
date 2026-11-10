// node --test (Node 22 strips the types) — CI runs this on Node 22 only (P2 plan G2).
import assert from "node:assert/strict";
import { test } from "node:test";

import { formatCents, parseDollars } from "./money.ts";

test("formatCents", () => {
  assert.equal(formatCents(842000), "$8,420.00");
  assert.equal(formatCents(5), "$0.05");
  assert.equal(formatCents(0), "$0.00");
  assert.equal(formatCents(150000), "$1,500.00");
  assert.equal(formatCents(123456789), "$1,234,567.89");
  assert.equal(formatCents(-2000), "-$20.00");
});

test("formatCents rejects non-integers", () => {
  assert.throws(() => formatCents(10.5), TypeError);
  assert.throws(() => formatCents(Number.NaN), TypeError);
});

test("parseDollars accepts what people type", () => {
  assert.equal(parseDollars("5000"), 500000);
  assert.equal(parseDollars("5,000"), 500000);
  assert.equal(parseDollars("$5,000.00"), 500000);
  assert.equal(parseDollars(" 5000.5 "), 500050);
  assert.equal(parseDollars("0.05"), 5);
  assert.equal(parseDollars("9999999.99"), 999999999);
});

test("parseDollars rejects everything else", () => {
  for (const bad of ["", " ", "abc", "-5", "1.001", "1e3", "5.", ".5", "10000000", "1,2,3.4.5", "NaN"]) {
    assert.equal(parseDollars(bad), null, bad);
  }
});

test("round trip", () => {
  for (const c of [1, 99, 100, 500000, 842000]) {
    assert.equal(parseDollars(formatCents(c)), c);
  }
});
