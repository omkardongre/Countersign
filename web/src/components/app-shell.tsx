import Link from "next/link";
import type { ReactNode } from "react";

const NAV = [
  { href: "/", label: "Case Queue" },
] as const;

export function AppShell({
  children,
  current,
}: {
  children: ReactNode;
  current?: string;
}) {
  return (
    <div className="min-h-dvh">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-paper-raised focus:px-3 focus:py-2 focus:shadow-md"
      >
        Skip to main content
      </a>

      {/* ── Header ── */}
      <header className="sticky top-0 z-20 border-b border-line bg-paper-raised/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-6 px-6">
          {/* Brand */}
          <Link href="/" className="flex items-center gap-2.5">
            <span className="flex h-7 w-7 items-center justify-center rounded-md bg-brand">
              <svg className="h-4 w-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
              </svg>
            </span>
            <span className="text-[15px] font-semibold tracking-tight text-ink">
              Countersign
            </span>
            <span className="hidden text-[11px] font-medium uppercase tracking-wider text-ink-faint sm:inline">
              Case Desk
            </span>
          </Link>

          {/* Nav */}
          <nav aria-label="Primary" className="flex items-center gap-1">
            {NAV.map((item) => {
              const active = current === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors ${
                    active
                      ? "bg-surface text-ink"
                      : "text-ink-muted hover:bg-surface hover:text-ink"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            <span className="hidden items-center gap-1.5 text-[12px] text-ink-faint md:flex">
              <span className="h-1.5 w-1.5 rounded-full bg-minor" />
              No agent holds decision authority
            </span>
            <Link
              href="/open"
              className="flex items-center gap-1.5 rounded-md bg-brand px-3.5 py-2 text-[13px] font-semibold text-white shadow-sm transition-colors hover:bg-brand-dark"
            >
              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
              </svg>
              New Case
            </Link>
          </div>
        </div>
      </header>

      {/* ── Environment notice ── */}
      <div className="border-b border-line bg-[#fdf8ee]">
        <div className="mx-auto flex max-w-[1400px] items-center gap-2 px-6 py-2">
          <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-warn" aria-hidden />
          <p className="text-[12px] text-ink-muted">
            Demo environment — pharmaceutical content is synthetic, but system integrations are real. This desk does not certify a batch.
          </p>
        </div>
      </div>

      {/* ── Main ── */}
      <main id="main" className="mx-auto max-w-[1400px] px-6 py-8">
        {children}
      </main>
    </div>
  );
}
