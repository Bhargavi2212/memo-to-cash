// node --test (Node 22 strips the types).
import assert from "node:assert/strict";
import { test } from "node:test";

import { askChips, messageChips } from "./chips.ts";

test("Northside's chips are the P4 ones, unchanged", () => {
  assert.deepEqual(askChips("Northside Jewelers"), [
    "Why is S-102 on my bill?",
    "What do I owe for M-207?",
  ]);
  assert.deepEqual(messageChips("Northside Jewelers"), [
    "S-104 has a chipped stone, please take $50 off",
    "I returned S-103 last week",
    "I'll pay the rest on Friday",
  ]);
});

test("each new buyer's chips name only its own records", () => {
  const own: Record<string, string[]> = {
    "Harbor Gems": ["S-130", "S-131", "S-132", "M-215"],
    "Lakeside Fine Jewelry": ["S-142", "S-150", "S-152", "M-216"],
  };
  for (const [buyer, refs] of Object.entries(own)) {
    const chips = [...askChips(buyer), ...messageChips(buyer)];
    assert.ok(chips.length === 5, buyer);
    for (const ref of chips.join(" ").match(/[SM]-\d{3}/g) ?? []) assert.ok(refs.includes(ref), ref);
    assert.ok(!chips.join(" ").includes("S-120")); // never the seeded request
  }
});

test("an unknown buyer gets no chips", () => {
  assert.deepEqual(askChips("Someone Else"), []);
  assert.deepEqual(messageChips("Someone Else"), []);
});
