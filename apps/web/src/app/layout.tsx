import { ClerkProvider, SignedIn, UserButton } from "@clerk/nextjs";
import type { Metadata } from "next";
import Link from "next/link";
import { GlobalSearch } from "@/components/GlobalSearch";
import "./globals.css";

export const metadata: Metadata = {
  title: "Smart Medical Vault",
  description: "Medical records for your whole family, in one place.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <ClerkProvider>
      <html lang="en">
        <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">
          <header className="border-b border-slate-200 bg-white">
            <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 py-3">
              <Link href="/profiles" className="shrink-0 font-semibold text-teal-800">
                Smart Medical Vault
              </Link>
              <SignedIn>
                <GlobalSearch />
                <UserButton />
              </SignedIn>
            </div>
          </header>
          <main className="mx-auto max-w-5xl px-4 py-8">{children}</main>
        </body>
      </html>
    </ClerkProvider>
  );
}
