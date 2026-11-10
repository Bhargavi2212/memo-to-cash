// Landing shell (P0). The judge path — "Start the demo", "See the supplier side",
// "Start again" — is wired in P5 (docs/UI_UX.md §4.5).
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
      <div className="mt-10 rounded-card border border-line bg-surface p-4 text-sm text-muted">
        The live demo opens here soon. Sandbox only — no real money moves.
      </div>
    </main>
  );
}
