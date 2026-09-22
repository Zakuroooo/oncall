/**
 * The naive-vs-sustained comparison, as a diagram.
 *
 * 🔴 This is the single most important thing on the landing page, so it is
 *    drawn rather than described. "A one-shot health check lies" is abstract
 *    in prose and obvious in a picture: one probe hits a flat line, thirty
 *    seconds of load hits a wall.
 *
 * Inline SVG, no chart library — the shape is fixed, and hand-drawing it costs
 * less than a dependency plus the code to configure it.
 */
export function ProbeCompare() {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <figure className="panel p-5">
        <figcaption className="mb-1 flex items-center gap-2">
          <span className="size-2 rounded-full bg-signal-red" aria-hidden="true" />
          <h3 className="text-[14px] font-semibold">The naive check</h3>
          <span className="ml-auto font-mono text-[11px] text-mist-500">1 probe · idle</span>
        </figcaption>
        <p className="mb-4 text-[13px] leading-relaxed text-mist-300">
          Restart the container, wait a moment, curl{" "}
          <code className="rounded bg-ink-600 px-1.5 py-0.5 font-mono text-[12px] text-signal-teal">/health</code>.
          It answers 200. Close the incident.
        </p>
        <svg viewBox="0 0 320 100" className="w-full" role="img"
             aria-label="A single probe against an idle service returns 200 OK.">
          <line x1="10" y1="70" x2="310" y2="70" stroke="var(--color-ink-500)" strokeWidth="1" />
          <text x="10" y="88" fill="var(--color-mist-500)" fontFamily="var(--font-mono-stack)" fontSize="9">t=0</text>
          <circle cx="46" cy="70" r="6" fill="var(--color-signal-green)" />
          <text x="60" y="74" fill="var(--color-signal-green)" fontFamily="var(--font-mono-stack)" fontSize="11">200 OK</text>
          <text x="60" y="52" fill="var(--color-mist-500)" fontSize="10">one request, nothing else running</text>
        </svg>
        <p className="mt-4 rounded-lg border border-signal-red/30 bg-signal-red/8 px-3 py-2 text-[12.5px] text-mist-300">
          <strong className="text-signal-red">The problem:</strong> an idle connection pool needs exactly
          one connection. So does one probe. The wall is still there — you just did not walk into it.
        </p>
      </figure>

      <figure className="panel border-signal-teal/40 p-5">
        <figcaption className="mb-1 flex items-center gap-2">
          <span className="size-2 rounded-full bg-signal-teal" aria-hidden="true" />
          <h3 className="text-[14px] font-semibold">The oracle</h3>
          <span className="ml-auto font-mono text-[11px] text-mist-500">3 probes · 30s · ~20 rps</span>
        </figcaption>
        <p className="mb-4 text-[13px] leading-relaxed text-mist-300">
          Hold roughly twenty requests a second against the service for thirty seconds,
          probing three times while the load runs.
        </p>
        <svg viewBox="0 0 320 100" className="w-full" role="img"
             aria-label="Three probes over thirty seconds under load: the third fails with 503.">
          <rect x="10" y="20" width="300" height="50" fill="var(--color-signal-teal)" opacity="0.07" />
          <line x1="10" y1="70" x2="310" y2="70" stroke="var(--color-ink-500)" strokeWidth="1" />
          <text x="10" y="88" fill="var(--color-mist-500)" fontFamily="var(--font-mono-stack)" fontSize="9">t=0</text>
          <text x="145" y="88" fill="var(--color-mist-500)" fontFamily="var(--font-mono-stack)" fontSize="9">t=15s</text>
          <text x="275" y="88" fill="var(--color-mist-500)" fontFamily="var(--font-mono-stack)" fontSize="9">t=30s</text>
          <text x="14" y="32" fill="var(--color-signal-teal)" fontFamily="var(--font-mono-stack)" fontSize="9">~20 rps sustained</text>
          <circle cx="105" cy="70" r="6" fill="var(--color-signal-green)" />
          <circle cx="200" cy="70" r="6" fill="var(--color-signal-amber)" />
          <circle cx="295" cy="70" r="6" fill="var(--color-signal-red)" />
          <text x="86" y="58" fill="var(--color-signal-green)" fontFamily="var(--font-mono-stack)" fontSize="9">200</text>
          <text x="178" y="58" fill="var(--color-signal-amber)" fontFamily="var(--font-mono-stack)" fontSize="9">slow</text>
          <text x="278" y="58" fill="var(--color-signal-red)" fontFamily="var(--font-mono-stack)" fontSize="9">503</text>
        </svg>
        <p className="mt-4 rounded-lg border border-signal-teal/30 bg-signal-teal/8 px-3 py-2 text-[12.5px] text-mist-300">
          <strong className="text-signal-teal">The verdict:</strong> not recovered. The pool wanted ten
          connections and the ceiling was five. The restart never touched it.
        </p>
      </figure>
    </div>
  );
}
