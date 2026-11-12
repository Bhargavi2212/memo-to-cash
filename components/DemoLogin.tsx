// Demo mode only: the shared PayPal sandbox buyer so a judge can pay (P2 D15, R9).
export function DemoLogin({ login }: { login: { email: string; password: string } }) {
  return (
    <aside className="rounded-card border border-dashed border-accent/40 bg-accent/5 p-4 text-sm">
      <p className="font-medium">Test PayPal login (no real money)</p>
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-muted">Email</dt>
        <dd className="break-all font-mono">{login.email}</dd>
        <dt className="text-muted">Password</dt>
        <dd className="break-all font-mono">{login.password}</dd>
      </dl>
      <p className="mt-3 text-muted">
        Can&apos;t log in? Choose &ldquo;Debit or Credit Card&rdquo; and use 4111 1111 1111 1111
        with any future date and any CVV.
      </p>
    </aside>
  );
}
