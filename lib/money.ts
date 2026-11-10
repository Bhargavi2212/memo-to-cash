// The only money formatter/parser in the frontend (CLAUDE.md §6, invariant 1).
// Money is integer cents everywhere; we format only at the UI edge and parse with string
// operations, never floating-point arithmetic.

const DOLLARS = /^\d{1,7}(\.\d{1,2})?$/; // up to $9,999,999.99 (P2 D15)

/** 842000 → "$8,420.00"; -2000 → "-$20.00". */
export function formatCents(cents: number): string {
  if (!Number.isInteger(cents)) throw new TypeError("formatCents takes integer cents");
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  const whole = Math.floor(abs / 100).toLocaleString("en-US");
  const frac = String(abs % 100).padStart(2, "0");
  return `${sign}$${whole}.${frac}`;
}

/** "5,000" / "$5000.5" / " 5000.50 " → 500050-style cents; anything else → null. */
export function parseDollars(text: string): number | null {
  const s = text.trim().replace(/[$,\s]/g, "");
  if (!DOLLARS.test(s)) return null;
  const [whole, frac = ""] = s.split(".");
  return Number(whole) * 100 + Number(frac.padEnd(2, "0"));
}
