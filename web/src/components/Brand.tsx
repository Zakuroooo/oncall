import Link from "next/link";

export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" className="shrink-0">
      <rect width="32" height="32" rx="8" fill="var(--color-ink-700)" />
      {/* A heartbeat trace: the product is a health probe, so the mark is one. */}
      <path
        d="M6 17h5l3-7 4 14 3-7h5"
        fill="none"
        stroke="var(--color-signal-teal)"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Wordmark({ href = "/", sub }: { href?: string; sub?: string }) {
  return (
    <Link href={href} className="flex items-center gap-2.5 group">
      <Logo />
      <span className="leading-tight">
        <span className="block font-semibold tracking-tight text-[15px] group-hover:text-signal-teal transition-colors">
          OnCall
        </span>
        {sub && <span className="block text-[11px] text-mist-500">{sub}</span>}
      </span>
    </Link>
  );
}
