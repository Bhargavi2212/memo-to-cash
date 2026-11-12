// Friendly words for raw values on the supplier pages (P3 a5 polish; UI_UX §5: plain words).
// PayPal's own status is stored raw (CLAUDE.md §4) — only its label is friendlier here.

const PAYPAL_STATUS: Record<string, string> = {
  DRAFT: "Draft",
  SENT: "Sent (updated)",
  UNPAID: "Unpaid",
  PAYMENT_PENDING: "Payment pending",
  PARTIALLY_PAID: "Partly paid",
  MARKED_AS_PAID: "Marked paid",
  PAID: "Paid",
  CANCELLED: "Cancelled",
  REFUNDED: "Refunded",
  PARTIALLY_REFUNDED: "Partly refunded",
};

export function paypalStatusLabel(s: string): string {
  return PAYPAL_STATUS[s] ?? s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g, " ");
}

const METHOD: Record<string, string> = {
  CHECK: "Cheque",
  WIRE_TRANSFER: "Wire",
  OTHER: "Other",
  PAYPAL: "PayPal",
};

/** How a payment came in: PayPal (statement / PayPal page) or the recorded method. */
export function paymentMethodLabel(source: string, method: string | null): string {
  if (source !== "OFF_PAYPAL") return "PayPal";
  return METHOD[method ?? ""] ?? "Other";
}
