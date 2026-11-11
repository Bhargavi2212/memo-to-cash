import type { Metadata } from "next";

// Supplier side (P3a): private, never indexed; wider than the buyer's page (UI_UX §3: 1280 px).
export const metadata: Metadata = {
  title: "Supplier · Memo-to-Cash",
  robots: { index: false, follow: false },
};

export default function SupplierLayout({ children }: LayoutProps<"/supplier">) {
  return (
    <main className="mx-auto w-full max-w-[1280px] flex-1 px-4 py-6 sm:py-10">{children}</main>
  );
}
