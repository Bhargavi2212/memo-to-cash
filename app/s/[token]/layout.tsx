import type { Metadata } from "next";

// Statement links are private: never indexed (P2 D14). The browser's default referrer policy
// (strict-origin-when-cross-origin) already keeps the token path out of cross-origin requests.
export const metadata: Metadata = {
  title: "Your statement · Memo-to-Cash",
  robots: { index: false, follow: false },
};

export default function StatementLayout({ children }: LayoutProps<"/s/[token]">) {
  return (
    <main className="mx-auto w-full max-w-[960px] flex-1 px-4 py-6 sm:py-10">{children}</main>
  );
}
