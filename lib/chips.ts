// Suggestion chips per demo buyer (P3 §M, PM-plan G6). Each buyer's chips name its own records,
// so the agent answers from real data instead of "S-102 isn't on your statement". Northside's
// chips are the P4 ones, unchanged (eval/README untouched). Unknown buyer → no chips.
// Pattern per buyer: ask about a RETURNED line and a memo; message about a KEPT line (deduction)
// and an OUT line ("I returned it"); never the seeded S-120 request (P4 R3).

const ASK: Record<string, string[]> = {
  "Northside Jewelers": ["Why is S-102 on my bill?", "What do I owe for M-207?"],
  "Harbor Gems": ["Why is S-131 on my bill?", "What do I owe for M-215?"],
  "Lakeside Fine Jewelry": ["Why is S-142 on my bill?", "What do I owe for M-216?"],
};

const MESSAGE: Record<string, string[]> = {
  "Northside Jewelers": [
    "S-104 has a chipped stone, please take $50 off",
    "I returned S-103 last week",
    "I'll pay the rest on Friday",
  ],
  "Harbor Gems": [
    "S-130 has a chipped stone, please take $50 off",
    "I returned S-132 last week",
    "I'll pay the rest on Friday",
  ],
  "Lakeside Fine Jewelry": [
    "S-150 has a chipped stone, please take $50 off",
    "I returned S-152 last week",
    "I'll pay the rest on Friday",
  ],
};

export const askChips = (buyer: string): string[] => ASK[buyer] ?? [];
export const messageChips = (buyer: string): string[] => MESSAGE[buyer] ?? [];
