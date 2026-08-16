import Link from "next/link";
import type { ReactNode } from "react";

const NAV = [
  { href: "/", label: "Queue" },
  { href: "/open", label: "Open case" },
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
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:bg-paper-raised focus:px-3 focus:py-2"
      >
        Skip to case content
      </a>
      <header className="border-b border-line bg-paper-raised">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-6 px-6 py-3">
          <div className="flex items-baseline gap-3">
            <Link href="/" className="text-[15px] font-semibold tracking-tight">
              Countersign
            </Link>
            <span className="text-ink-muted text-xs tracking-wide uppercase">
              Case desk
            </span>
          </div>
          <nav aria-label="Primary" className="flex gap-1 text-sm">
            {NAV.map((item) => {
              const active = current === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  className={`rounded-sm px-3 py-1.5 ${
                    active
                      ? "bg-teal-deep text-paper-raised"
                      : "text-ink-muted hover:text-ink"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <p className="hidden text-xs text-ink-muted sm:block">
            decision_authority{" "}
            <span className="font-mono text-ink">none</span>
          </p>
        </div>
      </header>
      <div className="border-b border-line bg-[#efe6c9] px-6 py-2 text-center text-[13px] text-ink">
        Pharmaceutical content is synthetic and labelled. System integrations
        are real. This desk does not certify a batch.
      </div>
      <div id="main" className="mx-auto max-w-7xl px-6 py-8">
        {children}
      </div>
    </div>
  );
}
