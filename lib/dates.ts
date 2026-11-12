// Relative dates for buyer screens ("sent 75d ago", "due in 11d"). Dates come from the API as
// YYYY-MM-DD; "today" is New York's date, like the backend (invariant 8).

const NY = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" });

export function todayNY(): string {
  return NY.format(new Date()); // en-CA formats as YYYY-MM-DD
}

function utcDay(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  return Date.UTC(y, m - 1, d) / 86_400_000;
}

/** Whole days from `iso` to today (positive = in the past). */
export function daysAgo(iso: string, today = todayNY()): number {
  return utcDay(today) - utcDay(iso);
}

/** Whole days from today to `iso` (positive = in the future). */
export function daysUntil(iso: string, today = todayNY()): number {
  return utcDay(iso) - utcDay(today);
}

/** "Nov 9" style short date for receipts. */
export function shortDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** shortDate for values that may be missing or carry a time ("2026-11-11T…") → "—" if none. */
export function longDate(iso: string | null | undefined): string {
  return iso ? shortDate(iso.slice(0, 10)) : "—";
}
