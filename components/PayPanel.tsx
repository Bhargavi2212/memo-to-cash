"use client";
// Pay: amount → live split preview → one PayPal checkout for several invoices
// (FR-4, FR-5; P2 D15, R3, R4, R9, R10, R14; plan G10, G24).
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ApiError, api, type PayRequest, type Preview, type Statement } from "@/lib/api";
import { formatCents, parseDollars } from "@/lib/money";
import { loadPayPal, type PayPalNamespace } from "@/lib/paypal";

import { DemoLogin } from "./DemoLogin";

const DEMO_AMOUNT = "5,000"; // R10: the demo story pays two invoices with one payment
const GENERIC_FAIL = "Payment didn't go through. You weren't charged.";

export function PayPanel({
  token,
  statement,
  selected,
  clientId,
  demoMode,
  onChanged,
}: {
  token: string;
  statement: Statement;
  selected: Set<number>;
  clientId: string | null;
  demoMode: boolean;
  onChanged: (message: string) => void;
}) {
  const router = useRouter();
  const demo = demoMode && statement.demo_login !== null;
  const [custom, setCustom] = useState(demo && statement.owed_cents >= 500000);
  const [amountText, setAmountText] = useState(DEMO_AMOUNT);
  // The last preview answer and the request it belongs to; "loading" = it's for an older one.
  const [result, setResult] = useState<{ key: string; preview: Preview | null; error: string | null }>(
    { key: "", preview: null, error: null },
  );
  const [banner, setBanner] = useState<string | null>(null);
  const [finishing, setFinishing] = useState(false);
  const [unconfirmed, setUnconfirmed] = useState<string | null>(null); // order id (R4)
  const [sdkFailed, setSdkFailed] = useState(false);

  // The PayPal callbacks are created once; they read the CURRENT request through refs.
  const requestRef = useRef<PayRequest>({});
  const validRef = useRef(false);
  const actionsRef = useRef<{ enable: () => void; disable: () => void } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const ids = [...selected];
  const amountCents = custom ? parseDollars(amountText) : null;
  const request: PayRequest = custom
    ? { invoice_ids: ids, amount_cents: amountCents ?? undefined }
    : { invoice_ids: ids };
  const localError =
    ids.length === 0
      ? "Choose at least one invoice."
      : custom && amountCents === null
        ? "Enter an amount like 5,000 or 1,250.50."
        : custom && amountCents === 0
          ? "Enter an amount above $0.00."
          : null;
  const key = JSON.stringify(request);
  const current = result.key === key;
  const preview = current ? result.preview : null;
  const previewError = current ? result.error : null;
  const loading = !localError && !current;

  // Debounced preview (300 ms) — the server computes the split (FR-4).
  useEffect(() => {
    requestRef.current = JSON.parse(key) as PayRequest;
    validRef.current = false;
    actionsRef.current?.disable();
    if (localError) return;
    const t = setTimeout(() => {
      api
        .preview(token, requestRef.current)
        .then((p) => {
          setResult({ key, preview: p, error: null });
          validRef.current = true;
          actionsRef.current?.enable();
        })
        .catch((e: unknown) =>
          setResult({ key, preview: null, error: e instanceof ApiError ? e.message : GENERIC_FAIL }),
        );
    }, 300);
    return () => clearTimeout(t);
  }, [key, localError, token]);

  async function finish(orderId: string, actions?: { restart: () => unknown }) {
    setFinishing(true);
    setBanner(null);
    try {
      const receipt = await api.capture(token, orderId);
      router.push(`/s/${token}/receipt/${receipt.payment_id}`);
    } catch (e) {
      setFinishing(false);
      if (!(e instanceof ApiError)) return setBanner(GENERIC_FAIL);
      if (e.code === "PAYMENT_DECLINED" && actions) return actions.restart();
      if (e.code === "CAPTURE_UNCONFIRMED") return setUnconfirmed(orderId);
      if (e.code === "STATEMENT_CHANGED") return onChanged(e.message);
      setBanner(e.message);
    }
  }

  // PayPal buttons, rendered once per client id.
  useEffect(() => {
    if (!clientId || !containerRef.current) return;
    let buttons: PayPalNamespace = null;
    let cancelled = false;
    loadPayPal(clientId)
      .then((paypal) => {
        if (cancelled || !containerRef.current) return;
        buttons = paypal.Buttons({
          style: { layout: "vertical", shape: "rect", label: "pay" },
          onInit: (_data: unknown, actions: { enable: () => void; disable: () => void }) => {
            actionsRef.current = actions;
            if (validRef.current) actions.enable();
            else actions.disable();
          },
          onClick: (_data: unknown, actions: { resolve: () => unknown; reject: () => unknown }) =>
            validRef.current ? actions.resolve() : actions.reject(),
          createOrder: async (data: { paymentSource?: string }) => {
            setBanner(null);
            try {
              const o = await api.order(token, {
                ...requestRef.current,
                funding_source: data.paymentSource,
              });
              return o.paypal_order_id;
            } catch (e) {
              setBanner(e instanceof ApiError ? e.message : GENERIC_FAIL);
              throw e;
            }
          },
          onApprove: (data: { orderID: string }, actions: { restart: () => unknown }) =>
            finish(data.orderID, actions),
          onError: () => setBanner((b) => b ?? GENERIC_FAIL),
          onCancel: () => undefined,
        });
        return buttons.render(containerRef.current);
      })
      .catch(() => setSdkFailed(true));
    return () => {
      cancelled = true;
      buttons?.close?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- created once; reads refs
  }, [clientId, token]);

  if (finishing)
    return (
      <div role="status" className="rounded-card border border-line bg-surface p-6 text-center">
        <p className="font-medium">Finishing your payment…</p>
        <p className="mt-1 text-sm text-muted">Please keep this page open.</p>
      </div>
    );

  const total = preview?.total_cents ?? 0;
  return (
    <section aria-labelledby="pay-h" className="space-y-4">
      <h2 id="pay-h" className="text-lg font-semibold">
        Pay
      </h2>

      <div className="rounded-card border border-line bg-surface p-4 space-y-3">
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="How much">
          <button
            type="button"
            role="radio"
            aria-checked={!custom}
            onClick={() => setCustom(false)}
            className={`min-h-11 rounded-card border px-3 text-sm ${!custom ? "border-accent bg-accent/10 text-accent" : "border-line"}`}
          >
            Selected invoices in full
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={custom}
            onClick={() => setCustom(true)}
            className={`min-h-11 rounded-card border px-3 text-sm ${custom ? "border-accent bg-accent/10 text-accent" : "border-line"}`}
          >
            Pay a different amount
          </button>
        </div>

        {custom && (
          <label className="block">
            <span className="text-sm text-muted">Amount (USD)</span>
            <span className="mt-1 flex min-h-11 items-center rounded-card border border-line px-3 focus-within:border-accent">
              <span className="text-muted">$</span>
              <input
                inputMode="decimal"
                className="tabular w-full bg-transparent px-1 py-2 outline-none"
                value={amountText}
                onChange={(e) => setAmountText(e.target.value)}
                aria-describedby="split-preview"
              />
            </span>
          </label>
        )}
        {demo && custom && amountText === DEMO_AMOUNT && (
          <p className="text-sm text-muted">
            Demo: one payment split across two invoices — or pay the full{" "}
            {formatCents(statement.owed_cents)}.
          </p>
        )}

        <div id="split-preview" aria-live="polite" className="min-h-12 text-sm">
          {localError ? (
            <p className="text-danger">{localError}</p>
          ) : previewError ? (
            <p className="text-danger">{previewError}</p>
          ) : loading && !preview ? (
            <p className="text-muted">Working out the split…</p>
          ) : preview ? (
            <>
              <p className="font-medium">
                Pay <span className="tabular">{formatCents(total)}</span> — split:
              </p>
              <ul className="mt-1 space-y-0.5">
                {preview.allocations.map((a) => {
                  const left = preview.remaining_after.find((r) => r.invoice_no === a.invoice_no);
                  return (
                    <li key={a.invoice_id} className="tabular">
                      {a.invoice_no} {formatCents(a.amount_cents)}
                      <span className="text-muted">
                        {" "}
                        → {left && left.due_cents > 0 ? `${formatCents(left.due_cents)} left` : "paid in full"}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </>
          ) : null}
        </div>
      </div>

      {banner && (
        <p role="alert" className="rounded-card border border-danger/30 bg-danger/5 px-4 py-3 text-sm text-danger">
          {banner}
        </p>
      )}

      {unconfirmed && (
        <div role="alert" className="rounded-card border border-warning/30 bg-warning/5 p-4 text-sm">
          <p>We couldn&apos;t confirm your payment yet — don&apos;t pay again.</p>
          <button
            type="button"
            onClick={() => {
              const id = unconfirmed;
              setUnconfirmed(null);
              void finish(id);
            }}
            className="mt-2 min-h-11 rounded-card bg-accent px-4 font-medium text-white"
          >
            Check again
          </button>
        </div>
      )}

      {!clientId ? (
        <p className="text-sm text-danger">Payments aren&apos;t configured on this server.</p>
      ) : sdkFailed ? (
        <div className="rounded-card border border-line bg-surface p-4 text-sm">
          <p>PayPal&apos;s buttons didn&apos;t load. You can still pay each invoice on PayPal:</p>
          <ul className="mt-2 list-disc pl-5">
            {statement.invoices
              .filter((i) => i.due_cents > 0 && i.paypal_url)
              .map((i) => (
                <li key={i.id}>
                  <a className="text-accent underline" href={i.paypal_url ?? "#"} target="_blank" rel="noopener noreferrer">
                    {i.invoice_no} on PayPal
                  </a>
                </li>
              ))}
          </ul>
        </div>
      ) : (
        <div className="relative">
          <div ref={containerRef} className="min-h-[140px]" />
          {(loading || localError || previewError) && (
            <p className="mt-2 text-center text-xs text-muted">
              {loading ? "Updating the split…" : "Fix the amount above to pay."}
            </p>
          )}
        </div>
      )}

      {statement.demo_login && <DemoLogin login={statement.demo_login} />}
    </section>
  );
}
