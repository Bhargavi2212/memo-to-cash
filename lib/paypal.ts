// Loads the PayPal JS SDK once (P2 D15; plan G12). Our own ~30 lines instead of
// @paypal/react-paypal-js: no new dependency, full control of the SDK URL. The client id
// comes from GET /api/config at runtime (D12) — no NEXT_PUBLIC_* build variable.

/* eslint-disable @typescript-eslint/no-explicit-any */
export type PayPalNamespace = any; // the SDK ships no types we can import without a dependency

const cache = new Map<string, Promise<PayPalNamespace>>();

export function sdkUrl(clientId: string): string {
  const q = new URLSearchParams({
    "client-id": clientId,
    components: "buttons",
    currency: "USD",
    intent: "capture",
    commit: "true",
    "enable-funding": "venmo,paylater",
    "buyer-country": "US", // sandbox only (invariant 6) — lets Venmo/Pay Later render
  });
  return `https://www.sandbox.paypal.com/sdk/js?${q.toString()}`;
}

/** Resolves with window.paypal; the same promise for the same URL (React may mount twice). */
export function loadPayPal(clientId: string): Promise<PayPalNamespace> {
  const url = sdkUrl(clientId);
  const hit = cache.get(url);
  if (hit) return hit;
  const p = new Promise<PayPalNamespace>((resolve, reject) => {
    const w = window as unknown as { paypal?: PayPalNamespace };
    if (w.paypal) return resolve(w.paypal);
    const s = document.createElement("script");
    s.src = url;
    s.async = true;
    s.onload = () => (w.paypal ? resolve(w.paypal) : reject(new Error("PayPal SDK loaded empty")));
    s.onerror = () => {
      cache.delete(url);
      reject(new Error("PayPal SDK failed to load"));
    };
    document.head.appendChild(s);
  });
  cache.set(url, p);
  return p;
}
