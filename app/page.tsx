// Landing (UI_UX §4.5, P2 D17). "Start the demo" (P2) and "See the supplier side" (P3 A10);
// final copy and polish in P5.
import { StartDemo } from "@/components/StartDemo";
import { SupplierEntry } from "@/components/SupplierEntry";

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-[960px] flex-1 flex-col justify-center px-4 py-16">
      <p className="text-sm font-medium text-accent">Memo-to-Cash</p>
      <h1 className="mt-3 text-3xl font-semibold leading-tight tracking-tight sm:text-4xl">
        Your buyers get one link: what they kept, what they returned, what they owe — and one
        button to pay it all on PayPal.
      </h1>
      <p className="mt-6 max-w-prose text-muted">
        For goods sent on memo. Every number traces back to a memo line, a return or a payment.
      </p>
      <div className="mt-10 space-y-4">
        <StartDemo />
        <SupplierEntry />
      </div>
      <p className="mt-10 text-xs text-muted">
        Sandbox only — no real money moves. The PayPal sandbox buyer login is shown on the
        statement page.
      </p>
    </main>
  );
}
