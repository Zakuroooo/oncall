import Link from "next/link";
import { Wordmark } from "@/components/Brand";
import { Badge, ButtonLink, btnStyles } from "@/components/ui";
import { Section, Rule } from "@/components/landing/Sections";
import { ProbeCompare } from "@/components/landing/ProbeCompare";
import { FAULT_CLASSES } from "@/lib/seed";
import { getSessionUser } from "@/lib/auth";

export default async function Landing() {
  // The nav adapts if you are already signed in — a "Sign up" button shown to
  // a signed-in user is the smallest possible sign that nobody used the thing.
  const user = await getSessionUser();

  return (
    <div className="relative">
      {/* ── Nav ───────────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-40 border-b border-ink-500/60 bg-ink-900/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-6 px-6 py-3">
          <Wordmark sub="self-healing infrastructure" />
          <nav className="ml-auto hidden items-center gap-6 text-[13.5px] text-mist-300 md:flex">
            <a href="#problem" className="hover:text-mist-100 transition-colors">The problem</a>
            <a href="#how" className="hover:text-mist-100 transition-colors">How it works</a>
            <a href="#faults" className="hover:text-mist-100 transition-colors">Fault classes</a>
            <a href="#start" className="hover:text-mist-100 transition-colors">Get started</a>
          </nav>
          <div className="ml-auto flex items-center gap-2 md:ml-0">
            {user ? (
              <ButtonLink href="/dashboard">Open console →</ButtonLink>
            ) : (
              <>
                <Link href="/login" className={btnStyles.quiet}>Sign in</Link>
                <ButtonLink href="/signup">Create account</ButtonLink>
              </>
            )}
          </div>
        </div>
      </header>

      {/* ── Hero ──────────────────────────────────────────────────────── */}
      <div className="relative overflow-hidden">
        <div className="aurora absolute inset-0 -z-10" aria-hidden="true" />
        <div className="gridlines absolute inset-0 -z-10" aria-hidden="true" />

        <Section className="!py-24">
          <div className="max-w-3xl rise">
            <Badge tone="teal">The model proposes · the oracle decides</Badge>
            <h1 className="mt-6 text-[42px] font-semibold leading-[1.08] tracking-tight sm:text-[58px]">
              Your agent said it fixed it.
              <br />
              <span className="text-signal-teal">Prove it.</span>
            </h1>
            <p className="mt-6 max-w-2xl text-[17px] leading-relaxed text-mist-300">
              OnCall is a self-healing infrastructure agent with one rule that makes it different:
              a language model is never allowed to decide whether a fix worked. Every verdict comes
              from a health probe that ran against the live service{" "}
              <strong className="text-mist-100">under load, for thirty seconds</strong>.
            </p>

            <div className="mt-9 flex flex-wrap items-center gap-3">
              <ButtonLink href={user ? "/dashboard" : "/signup"}>
                {user ? "Open the console" : "Create your account"}
              </ButtonLink>
              <Link href="#how" className={btnStyles.ghost}>See how it works</Link>
            </div>

            <div className="mt-8 flex flex-wrap items-center gap-x-6 gap-y-2 text-[12.5px] text-mist-500">
              <span>✓ Runs entirely on your machine</span>
              <span>✓ Nine allow-listed actions, no shell</span>
              <span>✓ A human approves before anything is touched</span>
            </div>
          </div>
        </Section>
      </div>

      <Rule />

      {/* ── The problem ───────────────────────────────────────────────── */}
      <Section
        id="problem"
        eyebrow="The problem"
        title="A health check that passes is not the same as a service that works."
        lede={
          <>
            Most self-healing agents stop at &ldquo;the action exited zero.&rdquo; The ones that check
            anything usually curl a health endpoint once and call it done. Here is a real failure
            where that answer is wrong — and it is wrong in the most dangerous way, by looking right.
          </>
        }
      >
        <div className="mb-8 panel p-5">
          <div className="mb-3 flex items-center gap-2">
            <Badge tone="bad">F4</Badge>
            <h3 className="text-[15px] font-semibold">Connection pool exhaustion</h3>
          </div>
          <p className="text-[14px] leading-relaxed text-mist-300">
            Postgres has <code className="rounded bg-ink-600 px-1.5 py-0.5 font-mono text-[12.5px] text-signal-teal">max_connections</code>{" "}
            set to 5 by an <code className="rounded bg-ink-600 px-1.5 py-0.5 font-mono text-[12.5px] text-signal-teal">ALTER SYSTEM</code>,
            which writes to <span className="font-mono text-[13px]">postgresql.auto.conf</span> — so it{" "}
            <strong className="text-mist-100">survives a restart</strong>. The agent restarts the app.
            The pool empties. An idle pool needs one connection, and one probe asks for one connection.
            <strong className="text-signal-red"> 200 OK.</strong> Then real traffic arrives, the pool
            wants ten, and the ceiling is still five.
          </p>
        </div>
        <ProbeCompare />
      </Section>

      <Rule />

      {/* ── How you use it — the requested step-by-step ───────────────── */}
      <Section
        id="how"
        eyebrow="How you use it"
        title="Five steps, start to verdict."
        lede="Everything runs locally. The victim stack is four containers OnCall gives you, so you can break things on purpose without breaking anything real."
      >
        <ol className="space-y-4">
          {STEPS.map((step, i) => (
            <li key={step.title} className="panel flex flex-col gap-4 p-5 sm:flex-row sm:items-start">
              <div className="flex size-9 shrink-0 items-center justify-center rounded-full border border-signal-teal/40 bg-signal-teal/10 font-mono text-[15px] font-semibold text-signal-teal">
                {i + 1}
              </div>
              <div className="min-w-0 flex-1">
                <h3 className="text-[15px] font-semibold">{step.title}</h3>
                <p className="mt-1.5 text-[13.5px] leading-relaxed text-mist-300">{step.body}</p>
                {step.command && (
                  <pre className="mt-3 overflow-x-auto rounded-lg border border-ink-500 bg-ink-950 px-3.5 py-2.5 font-mono text-[12.5px] text-signal-teal">
                    {step.command}
                  </pre>
                )}
                {step.note && (
                  <p className="mt-3 rounded-lg border border-ink-500 bg-ink-700/50 px-3 py-2 text-[12.5px] text-mist-500">
                    {step.note}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ol>
      </Section>

      <Rule />

      {/* ── Fault classes ─────────────────────────────────────────────── */}
      <Section
        id="faults"
        eyebrow="The corpus"
        title="Five failures, each asking a different question."
        lede="These were not picked for variety. Only the first one is fixed by a restart — the other four exist to catch an agent that has learned to restart things and call it a day."
      >
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {FAULT_CLASSES.map((f) => (
            <article key={f.id} className="panel flex flex-col gap-3 p-5">
              <div className="flex items-center gap-2">
                <Badge tone={f.id === "F4" ? "bad" : "idle"}>{f.id}</Badge>
                <h3 className="text-[14.5px] font-semibold">{f.title}</h3>
              </div>
              <p className="text-[13px] leading-relaxed text-mist-300">{f.blurb}</p>
              <p className="font-mono text-[11.5px] leading-relaxed text-mist-500">{f.symptom}</p>
              <p className="mt-auto border-t border-ink-500 pt-3 text-[12.5px] italic leading-relaxed text-mist-300">
                {f.question}
              </p>
              <div className="flex flex-wrap gap-1.5">
                {f.restartFixes
                  ? <Badge tone="good">restart fixes it</Badge>
                  : <Badge tone="wait">restart will not fix it</Badge>}
                {f.loadDependent && <Badge tone="bad">load-dependent</Badge>}
              </div>
            </article>
          ))}
        </div>
      </Section>

      <Rule />

      {/* ── Architecture / trust boundary ─────────────────────────────── */}
      <Section
        eyebrow="Architecture"
        title="Two tiers, and only one of them can touch anything."
        lede="This split is the security design, not a deployment convenience. The tier that can restart containers never leaves your machine."
      >
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="panel p-5">
            <div className="mb-2 flex items-center gap-2">
              <Badge tone="work">Web tier</Badge>
              <span className="font-mono text-[11.5px] text-mist-500">this website</span>
            </div>
            <h3 className="text-[15px] font-semibold">Reads. Never acts.</h3>
            <p className="mt-2 text-[13.5px] leading-relaxed text-mist-300">
              Accounts, sessions, the landing page, and a read-only view of incidents, verdicts and
              metrics. It queries Postgres and it calls the control plane over HTTP. It has no Docker
              socket, no shell, and no write path into an incident.
            </p>
          </div>
          <div className="panel border-signal-amber/40 p-5">
            <div className="mb-2 flex items-center gap-2">
              <Badge tone="wait">Control plane</Badge>
              <span className="font-mono text-[11.5px] text-mist-500">127.0.0.1 only</span>
            </div>
            <h3 className="text-[15px] font-semibold">Acts. Never exposed.</h3>
            <p className="mt-2 text-[13.5px] leading-relaxed text-mist-300">
              The agent loop, the action broker, and the oracle. It holds the Docker socket, which is
              root-equivalent, so its listen address is a hard-coded constant rather than a setting.
              You cannot misconfigure it onto a public interface, because there is no configuration to get wrong.
            </p>
          </div>
        </div>

        <div className="mt-4 panel p-5">
          <h3 className="mb-3 text-[14px] font-semibold">Before any action runs, it passes five gates</h3>
          <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-5">
            {GATES.map((g, i) => (
              <div key={g.q} className="rounded-lg border border-ink-500 bg-ink-700/60 p-3">
                <div className="font-mono text-[11px] font-bold text-signal-teal">L{i + 1}</div>
                <div className="mt-1 text-[12.5px] leading-snug text-mist-300">{g.q}</div>
              </div>
            ))}
          </div>
          <p className="mt-4 text-[12.5px] leading-relaxed text-mist-500">
            Nine actions are allow-listed by name. There is no shell anywhere in the action path — no{" "}
            <span className="font-mono">subprocess</span>, no <span className="font-mono">os.system</span>,
            no <span className="font-mono">shell=True</span> — and a grep in the build enforces that,
            so the rule cannot quietly stop being true.
          </p>
        </div>
      </Section>

      <Rule />

      {/* ── Get started ───────────────────────────────────────────────── */}
      <Section id="start" className="!pb-28">
        <div className="panel aurora relative overflow-hidden p-10 text-center">
          <h2 className="text-[30px] font-semibold tracking-tight">Watch a fix fail its own health check.</h2>
          <p className="mx-auto mt-4 max-w-xl text-[15px] leading-relaxed text-mist-300">
            Create an account to open the console. Until your own control plane is running you will see
            recorded sample runs — clearly marked as samples on every screen, never dressed up as yours.
          </p>
          <div className="mt-8 flex flex-wrap justify-center gap-3">
            <ButtonLink href={user ? "/dashboard" : "/signup"}>
              {user ? "Open the console" : "Create your account"}
            </ButtonLink>
            {!user && <Link href="/login" className={btnStyles.ghost}>I already have one</Link>}
          </div>
        </div>
      </Section>

      <footer className="border-t border-ink-500 py-8">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-4 px-6 text-[12.5px] text-mist-500">
          <Wordmark sub="the model proposes · the oracle decides" />
          <span className="ml-auto">Verdict contract: 3 probes · 30s window · ~20 rps · zero tokens</span>
        </div>
      </footer>
    </div>
  );
}

/* The five steps. Kept as data next to the page that renders them — this list
   is the product's onboarding, and it should be obvious where to edit it. */
const STEPS = [
  {
    title: "Start the victim stack",
    body: "Four containers: a small shop app, its Postgres, its Redis, and OnCall's own database. The shop app's /health does real work — a real query, a real Redis ping, a real disk write — so it cannot pass while its dependencies are broken.",
    command: "make up && make migrate-write && make seed-write",
    note: "A health endpoint that just returns {\"status\":\"ok\"} would make this whole project impossible: the question \"did the fix work?\" would always answer yes.",
  },
  {
    title: "Run the control plane",
    body: "The API and the worker start together and bind to loopback. The API refuses to boot if the database schema is incomplete, so a half-migrated setup fails in four seconds rather than halfway through your first incident.",
    command: "make serve",
  },
  {
    title: "Break something on purpose",
    body: "Pick one of the five fault classes. The injector really does break the stack — it stops a container, fills a disk, takes a lock, or rewrites a Postgres setting — and it opens an incident. Every fault is reversible from the same screen.",
    command: "make inject-write F=F4",
    note: "Start with F4 if you want to see the point of the project in one run.",
  },
  {
    title: "Approve the proposal",
    body: "The agent reads the logs, diagnoses the failure, retrieves matching runbooks, and proposes exactly one allow-listed action with exact parameters. Then it stops and waits for you. That pause is a row in the database, not a blocked thread — you can close the laptop and approve tomorrow.",
  },
  {
    title: "Read the verdict",
    body: "After the action runs, the oracle takes a naive one-shot probe on the idle service, then holds ~20 rps for thirty seconds and probes three more times. Both results are recorded. When they disagree, that disagreement is the headline — it is a false positive that a conventional agent would have shipped as a success.",
  },
];

const GATES = [
  { q: "Is this action on the allow-list?" },
  { q: "Is this service one we are permitted to touch?" },
  { q: "Are the parameters the right type and in range?" },
  { q: "Did a human actually approve this exact action?" },
  { q: "Are we still under the attempt limit?" },
];
