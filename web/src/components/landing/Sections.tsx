import type { ReactNode } from "react";

export function Section({
  id, eyebrow, title, lede, children, className = "",
}: {
  id?: string; eyebrow?: string; title?: string; lede?: ReactNode;
  children: ReactNode; className?: string;
}) {
  return (
    <section id={id} className={`mx-auto w-full max-w-6xl px-6 py-20 ${className}`}>
      {(eyebrow || title) && (
        <header className="mb-10 max-w-3xl">
          {eyebrow && (
            <div className="mb-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-signal-teal">
              {eyebrow}
            </div>
          )}
          {title && (
            <h2 className="text-[28px] font-semibold leading-tight tracking-tight sm:text-[34px]">
              {title}
            </h2>
          )}
          {lede && <p className="mt-4 text-[15px] leading-relaxed text-mist-300">{lede}</p>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Rule() {
  return <div className="mx-auto h-px w-full max-w-6xl bg-gradient-to-r from-transparent via-ink-500 to-transparent" />;
}
