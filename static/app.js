/* ═══════════════════════════════════════════════════════════════════════════
   OnCall console — vanilla JS, no build step (D14).

   Sections:
     1. Utilities        — escaping, formatting, DOM helpers
     2. App state        — the four things that survive between renders
     3. Transport        — api(), the banner, toasts
     4. View router
     5. Overview         — hero stats, topology
     6. Incidents list
     7. Incident detail  — stepper, timeline, SSE, verdict
     8. Approval gate
     9. Evidence
    10. Chaos lab
    11. Boot + polling

   Two invariants worth stating up front, because most of the bugs this file
   was rewritten to fix were violations of one of them:

   🔴 (a) EVERY value interpolated into HTML goes through esc(). No exceptions
          for "it's a number" — payload fields come out of JSONB that was
          written from model output, and a field being numeric today is not a
          guarantee about tomorrow.

   🔴 (b) EVERY async render checks its generation token before writing to the
          DOM. Without it, a slow response from the incident you just left
          paints itself over the incident you are now looking at.
   ═══════════════════════════════════════════════════════════════════════════ */
"use strict";

/* ── 1. Utilities ──────────────────────────────────────────────────────── */

const $ = (id) => document.getElementById(id);

const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC_MAP[c]);

/** Time, plus the date when it is not today — an incident from Tuesday and one
 *  from two minutes ago must not read identically (F-15). */
function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay ? t : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${t}`;
}

/** "4m 12s" — durations read faster than raw seconds on a console. */
function fmtDuration(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

const pct = (v) => (v == null ? "—" : `${v}%`);

/** A "nothing here yet" block. Every async surface gets one, so an empty
 *  result never looks like a silent failure (F-12). */
const emptyState = (title, hint) =>
  `<div class="empty"><div class="empty-title">${esc(title)}</div>${hint ? esc(hint) : ""}</div>`;

/* ── 2. App state ──────────────────────────────────────────────────────── */

const state = {
  view: "overview",
  incidentId: null,
  /** Bumped on every navigation. An async render whose token no longer matches
   *  has been superseded and must not touch the DOM (invariant (b)). */
  gen: 0,
  filter: "all",
  stream: null,
  /** Event ids already appended, so a reconnect that replays cannot duplicate
   *  a row. The server now resumes from Last-Event-ID (see routes_stream.py),
   *  which is the real fix; this set is the client-side belt to that braces. */
  seenEvents: new Set(),
  lastServices: [],
};

const nextGen = () => ++state.gen;
const isCurrent = (token) => token === state.gen;

/* ── 3. Transport ──────────────────────────────────────────────────────── */

async function api(path, opts) {
  const resp = await fetch(path, opts);
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      if (body && body.detail) detail = typeof body.detail === "string" ? body.detail : detail;
    } catch { /* non-JSON error body — the status code is enough */ }
    throw new Error(detail);
  }
  return resp.json();
}

/** The global failure surface.
 *  🔴 It is deliberately NOT inside any panel. The old UI wrote "API down" into
 *  the fault-injector's status line, so a database outage looked like a failed
 *  injection — the operator debugged the wrong thing (F-11). */
function showBanner(msg) {
  $("banner-text").textContent = msg;
  $("banner").hidden = false;
}
const hideBanner = () => { $("banner").hidden = true; };

function setConn(status, label) {
  const el = $("conn");
  el.classList.toggle("is-live", status === "live");
  el.classList.toggle("is-down", status === "down");
  $("conn-label").textContent = label;
}

function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

/** Promise-based confirm dialog for destructive actions (F-09).
 *  window.confirm() would do, but it blocks the event loop, cannot be styled,
 *  and browsers are increasingly free to suppress it. */
function confirmAction(title, body, confirmLabel = "Confirm") {
  return new Promise((resolve) => {
    const modal = $("confirm");
    $("confirm-title").textContent = title;
    $("confirm-body").textContent = body;
    $("confirm-yes").textContent = confirmLabel;
    modal.hidden = false;
    $("confirm-yes").focus();

    const done = (answer) => {
      modal.hidden = true;
      $("confirm-yes").onclick = null;
      $("confirm-no").onclick = null;
      document.removeEventListener("keydown", onKey);
      resolve(answer);
    };
    const onKey = (e) => { if (e.key === "Escape") done(false); };

    $("confirm-yes").onclick = () => done(true);
    $("confirm-no").onclick = () => done(false);
    document.addEventListener("keydown", onKey);
  });
}

/* ── 4. View router ────────────────────────────────────────────────────── */

const VIEWS = ["overview", "incidents", "detail", "evidence", "chaos"];
const CRUMBS = {
  overview: "Overview", incidents: "Incidents",
  evidence: "Evidence", chaos: "Chaos lab",
};

function showView(name, incidentId = null) {
  const token = nextGen();

  // 🔴 Close the stream and forget the incident BEFORE anything else. Leaving
  //    incidentId set meant a pending "refresh after the stream ended" timer
  //    still matched and re-opened a stream on a view nobody was looking at (F-03).
  closeStream();
  state.view = name;
  state.incidentId = name === "detail" ? incidentId : null;

  VIEWS.forEach((v) => { $("view-" + v).hidden = v !== name; });

  const navTarget = name === "detail" ? "incidents" : name;
  document.querySelectorAll(".nav-item").forEach((b) => {
    const on = b.dataset.view === navTarget;
    b.classList.toggle("is-active", on);
    if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });

  $("crumbs").innerHTML = name === "detail"
    ? `Incidents / <b>#${esc(incidentId)}</b>`
    : `<b>${esc(CRUMBS[name])}</b>`;

  window.scrollTo({ top: 0 });

  if (name === "overview") loadOverview(token);
  else if (name === "incidents") loadIncidents(token);
  else if (name === "evidence") loadEvidence(token);
  else if (name === "chaos") loadChaos(token);
  else if (name === "detail") openIncident(incidentId, token);
}

document.querySelectorAll("[data-view]").forEach((btn) =>
  btn.addEventListener("click", () => showView(btn.dataset.view)));

$("btn-back").addEventListener("click", () => showView("incidents"));
$("banner-x").addEventListener("click", hideBanner);
$("btn-refresh").addEventListener("click", () => showView(state.view, state.incidentId));

/* ── 5. Overview ───────────────────────────────────────────────────────── */

async function loadOverview(token) {
  try {
    const [services, incidents, stats] = await Promise.all([
      api("/api/services"),
      api("/api/incidents?limit=50"),
      api("/api/stats").catch(() => null),   // stats are optional context here
    ]);
    if (!isCurrent(token)) return;

    state.lastServices = services;
    hideBanner();
    setConn("live", "connected");
    renderHeroStats(incidents, stats);
    renderTopology(services);
    renderActiveIncidents(incidents);
    updateOpenCount(incidents);
  } catch (e) {
    if (!isCurrent(token)) return;
    setConn("down", "no connection");
    showBanner(`Control plane unreachable — ${e.message}`);
  }
}

function renderHeroStats(incidents, stats) {
  const open = incidents.filter((i) => !["RECOVERED", "ESCALATED"].includes(i.state));
  const waiting = incidents.filter((i) => i.state === "AWAITING_APPROVAL");
  const recovered = incidents.filter((i) => i.state === "RECOVERED");

  // 🔥 The project's headline. Summing COUNTS across classes is fine — the rule
  // METRICS.md §9 forbids is an aggregate RATE, because a rate over unequal
  // classes can be tuned by changing the mix. A count of caught false
  // positives cannot be.
  const disagreements = (stats?.disagreements || [])
    .reduce((sum, r) => sum + Number(r.disagreements || 0), 0);

  const card = (v, k, cls = "") =>
    `<div class="stat ${cls}"><div class="stat-v">${esc(v)}</div><div class="stat-k">${esc(k)}</div></div>`;

  $("hero-stats").innerHTML = [
    card(disagreements, "false positives caught", "s-hero"),
    card(open.length, "open now", open.length ? "s-warn" : ""),
    card(waiting.length, "awaiting you", waiting.length ? "s-warn" : ""),
    card(recovered.length, "recovered", "s-good"),
  ].join("");
}

/** Is this container state a healthy one? Anchored, not a substring match —
 *  "not_found (NotFound)" must never match a rule meant for "running". */
const isUpState = (s) => /^(running|healthy|ok)\b/i.test(String(s || ""));

/** shopapp's /health returns per-subsystem strings that start with "ok" or
 *  "FAIL". Classifying once, here, keeps the mapping in one place. */
function checkClass(value) {
  const v = String(value || "");
  if (/^ok/i.test(v)) return "c-ok";
  if (/^fail/i.test(v)) return "c-fail";
  return "";
}

/**
 * The topology map.
 *
 * 🔴 This is the one view that makes the project's hardest fault legible.
 *    Under F5 the container that looks broken is shopapp, but the container
 *    that IS broken is shopcache. A flat list of four cards shows you two red
 *    boxes and no relationship. Drawing the dependency edge — and colouring it
 *    from shopapp's own /health checks — shows you which way the failure flows,
 *    which is exactly the localisation question the agent has to answer.
 */
function renderTopology(services) {
  const by = Object.fromEntries(services.map((s) => [s.service, s]));
  const app = by["shopapp"] || {};
  const checks = app.checks || {};

  const node = (svc, role) => {
    const s = by[svc];
    if (!s) return "";
    const up = isUpState(s.state);
    const cls = s.state === "unknown" ? "n-idle" : up ? "n-good" : "n-bad";
    const checkPills = svc === "shopapp"
      ? `<div class="checks">${Object.entries(checks).map(([k, v]) =>
          `<span class="check ${checkClass(v)}" title="${esc(v)}">${esc(k)}</span>`).join("")}</div>`
      : "";
    return `<div class="node ${cls}">
      <div class="node-top">
        <span class="node-name">${esc(s.service)}</span>
        <span class="badge ${up ? "b-good" : "b-bad"}">${esc(s.state)}</span>
      </div>
      <div class="node-role">${esc(role)}</div>
      ${s.detail ? `<div class="node-detail">${esc(s.detail)}</div>` : ""}
      ${checkPills}
    </div>`;
  };

  const edge = (label, checkValue) => {
    const k = checkClass(checkValue);
    const cls = k === "c-ok" ? "e-ok" : k === "c-fail" ? "e-fail" : "";
    return `<div class="edge-line">
      <span class="edge-label ${k === "c-fail" ? "l-fail" : ""}">${esc(label)}${
        k === "c-fail" ? " ✕" : k === "c-ok" ? " ✓" : ""}</span>
      <span class="edge-track ${cls}"></span>
    </div>`;
  };

  // Any service the API reported that is not part of the victim topology —
  // oncall-db, or a "docker-daemon: unreachable" row. Shown, never hidden:
  // a Docker daemon that is down explains every other card on the page.
  const extras = services
    .filter((s) => !["shopapp", "shopdb", "shopcache"].includes(s.service))
    .map((s) => node(s.service, s.service === "oncall-db" ? "control plane" : "infrastructure"))
    .join("");

  $("topo").innerHTML = `<div class="topo-grid">
    <div class="topo-col">${node("shopapp", "front door · deep health")}${extras}</div>
    <div class="edge">${edge("db", checks.db)}${edge("cache", checks.cache)}</div>
    <div class="topo-col">${node("shopdb", "postgres")}${node("shopcache", "redis")}</div>
  </div>`;
}

function renderActiveIncidents(incidents) {
  const open = incidents.filter((i) => !["RECOVERED", "ESCALATED"].includes(i.state));
  renderListPreservingFocus($("active-incidents"), open.length
    ? incidentTable(open.slice(0, 6))
    : emptyState("Nothing on fire", "Inject a fault from the Chaos lab to generate an incident."));
}

function updateOpenCount(incidents) {
  const waiting = incidents.filter((i) => i.state === "AWAITING_APPROVAL").length;
  const el = $("nav-open-count");
  el.textContent = String(waiting);
  el.hidden = waiting === 0;
}

/* ── 6. Incidents list ─────────────────────────────────────────────────── */

const STATE_BADGE = {
  RECOVERED: "b-good", ESCALATED: "b-bad", AWAITING_APPROVAL: "b-wait",
};

/** Human labels for the machine's state names.
 *  SCREAMING_SNAKE_CASE is the right name in the database and in the state
 *  machine, and the wrong thing to put in a table cell — "AWAITING_APPROVAL"
 *  is 17 characters that overflow their column and read like a log line.
 *  The raw value stays available as the element's title, because when you are
 *  debugging you want the exact enum member, not a prettified version. */
const STATE_LABEL = {
  DETECTED: "Detected", COLLECTING: "Collecting", DIAGNOSING: "Diagnosing",
  RETRIEVING: "Retrieving", PROPOSING: "Proposing", AWAITING_APPROVAL: "Awaiting you",
  EXECUTING: "Executing", VERIFYING: "Verifying",
  RECOVERED: "Recovered", ESCALATED: "Escalated",
};

const badgeFor = (s) =>
  `<span class="badge ${STATE_BADGE[s] || "b-work"}" title="${esc(s)}">${esc(STATE_LABEL[s] || s)}</span>`;

const FAULT_TITLES = {
  F1: "Process crash", F2: "Disk full", F3: "DB lock",
  F4: "Pool exhaustion", F5: "Dependency down",
};

function incidentTable(rows) {
  const head = `<div class="inc-head">
    <div>id</div><div>fault</div><div>state</div><div>attempt</div><div>rag</div><div>detected</div>
  </div>`;
  const body = rows.map((r) => `
    <button class="inc-row" data-id="${esc(r.id)}">
      <span class="inc-id">#${esc(r.id)}</span>
      <span>
        <span class="inc-title">${esc(r.fault_class)} · ${esc(FAULT_TITLES[r.fault_class] || "incident")}</span>
        <span class="inc-sub">${esc(r.service || "")}</span>
      </span>
      <span>${badgeFor(r.state)}</span>
      <span class="inc-cell">${esc(r.attempt)}</span>
      <span class="inc-cell">${r.rag_enabled ? "on" : "off"}</span>
      <span class="inc-cell">${esc(fmtTime(r.detected_at))}</span>
    </button>`).join("");
  return head + body;
}

/** 🔴 Rows are <button>, not <tr> with a click listener.
 *  The old markup was keyboard-dead: no tabindex, no Enter handler, so an
 *  incident could not be opened without a mouse (F-13). A button is focusable,
 *  Enter/Space-activatable, and announced as a control, for free. */
function wireIncidentRows(root) {
  root.querySelectorAll(".inc-row").forEach((row) =>
    row.addEventListener("click", () => showView("detail", Number(row.dataset.id))));
}

/**
 * Re-render a list without stealing the user's place in it.
 *
 * 🔴 This exists because of a bug the rewrite introduced and a live keyboard
 *    test caught: these lists refresh every 5 seconds by replacing innerHTML,
 *    which destroys the focused element. A keyboard user tabbing down the
 *    incident list was thrown back to the top of the page twice a minute.
 *
 *    Mouse users never see it, which is exactly why it survives code review
 *    and only turns up when someone actually tries to use the keyboard.
 *
 *    Note this restores focus by incident id, not by position — if a new
 *    incident arrives and shifts the rows, focus follows the incident the user
 *    was on, not whatever slid into that slot.
 */
function renderListPreservingFocus(container, html) {
  const active = document.activeElement;
  const keepId = active && container.contains(active) && active.classList.contains("inc-row")
    ? active.dataset.id
    : null;

  container.innerHTML = html;
  wireIncidentRows(container);

  if (keepId) {
    const again = container.querySelector(`.inc-row[data-id="${CSS.escape(keepId)}"]`);
    if (again) again.focus({ preventScroll: true });
  }
}

const FILTERS = {
  all: () => true,
  open: (i) => !["RECOVERED", "ESCALATED"].includes(i.state),
  recovered: (i) => i.state === "RECOVERED",
  escalated: (i) => i.state === "ESCALATED",
};

async function loadIncidents(token) {
  try {
    const rows = await api("/api/incidents?limit=200");
    if (!isCurrent(token)) return;
    hideBanner();
    setConn("live", "connected");
    updateOpenCount(rows);

    const shown = rows.filter(FILTERS[state.filter]);
    renderListPreservingFocus($("incidents-list"), shown.length
      ? incidentTable(shown)
      : emptyState(`No ${state.filter === "all" ? "" : state.filter} incidents`,
                   "Inject a fault from the Chaos lab to generate one."));
  } catch (e) {
    if (!isCurrent(token)) return;
    setConn("down", "no connection");
    showBanner(`Could not load incidents — ${e.message}`);
    $("incidents-list").innerHTML = emptyState("Could not load", e.message);
  }
}

document.querySelectorAll(".chip[data-filter]").forEach((chip) =>
  chip.addEventListener("click", () => {
    state.filter = chip.dataset.filter;
    document.querySelectorAll(".chip[data-filter]").forEach((c) =>
      c.classList.toggle("is-active", c === chip));
    loadIncidents(nextGen());
  }));

/* ── 7. Incident detail ────────────────────────────────────────────────── */

/** The pipeline, in order. AWAITING_APPROVAL sits in the middle on purpose —
 *  the human gate is a stage of the machine, not an interruption to it. */
const PIPELINE = [
  ["DETECTED", "detect"], ["COLLECTING", "collect"], ["DIAGNOSING", "diagnose"],
  ["RETRIEVING", "retrieve"], ["PROPOSING", "propose"], ["AWAITING_APPROVAL", "approve"],
  ["EXECUTING", "execute"], ["VERIFYING", "verify"], ["RECOVERED", "verdict"],
];

function renderStepper(incident) {
  const cur = String(incident.state);
  const escalated = cur === "ESCALATED";
  const idx = PIPELINE.findIndex(([s]) => s === cur);

  $("stepper").innerHTML = PIPELINE.map(([stateName, label], i) => {
    let cls = "";
    if (escalated) {
      // On an escalation we do not know which stage failed from the state
      // alone, so mark everything before the current attempt as done and put
      // the failure on the last step. The timeline below carries the detail.
      cls = i === PIPELINE.length - 1 ? "fail" : "done";
    } else if (cur === "RECOVERED") {
      cls = "done";
    } else if (i < idx) {
      cls = "done";
    } else if (i === idx) {
      cls = stateName === "AWAITING_APPROVAL" ? "wait" : "now";
    }
    const mark = cls === "done" ? "✓" : cls === "fail" ? "✕" : "";
    return `<li class="step ${cls}">
      <span class="step-dot" aria-hidden="true">${mark}</span>
      <span class="step-label">${esc(label)}</span>
    </li>`;
  }).join("");

  $("stepper-note").textContent = escalated
    ? "escalated — see the timeline for which stage stopped it"
    : cur === "RECOVERED" ? "closed by the oracle" : `currently ${cur.toLowerCase()}`;
}

async function openIncident(id, token) {
  state.seenEvents.clear();
  $("timeline").innerHTML = "";
  $("verdicts").innerHTML = "";
  $("verdict-panel").hidden = true;
  $("approval").hidden = true;
  $("det-title").textContent = `Incident #${id}`;
  $("det-meta").innerHTML = `<span>loading…</span>`;

  try {
    const detail = await api(`/api/incidents/${id}`);
    if (!isCurrent(token)) return;      // invariant (b)
    renderDetail(detail, token);
    openStream(id, token);
  } catch (e) {
    if (!isCurrent(token)) return;
    // 🔴 A failed load must never leave the previous incident's data on screen.
    //    Stale data looks exactly like fresh data to an operator.
    closeStream();
    setConn("down", "no connection");
    $("det-meta").innerHTML = `<span>load failed: ${esc(e.message)}</span>`;
    $("timeline").innerHTML = emptyState("Could not load this incident", e.message);
  }
}

function renderDetail(d, token) {
  const inc = d.incident;

  $("det-title").textContent = `Incident #${inc.id} — ${FAULT_TITLES[inc.fault_class] || inc.fault_class}`;
  $("det-badge").innerHTML = badgeFor(inc.state);

  const tokens = d.tokens
    ? `${Number(d.tokens.prompt_tokens || 0) + Number(d.tokens.completion_tokens || 0)} tokens · ${esc(d.tokens.calls)} calls`
    : "no LLM calls yet";
  const elapsed = inc.detected_at
    ? (new Date(inc.closed_at || Date.now()) - new Date(inc.detected_at)) / 1000
    : null;

  $("det-meta").innerHTML = [
    `${esc(inc.fault_class)}`, `service ${esc(inc.service)}`, `attempt ${esc(inc.attempt)}`,
    `RAG ${inc.rag_enabled ? "on" : "off"}`, `seed ${esc(inc.seed)}`,
    tokens, `${inc.closed_at ? "took" : "open"} ${esc(fmtDuration(elapsed))}`,
  ].map((s) => `<span>${s}</span>`).join("");

  renderStepper(inc);
  renderVerdicts(d.verdicts || []);

  $("timeline").innerHTML = "";
  state.seenEvents.clear();
  (d.timeline || []).forEach((ev) => appendEvent(ev));
  $("timeline-note").textContent = `${(d.timeline || []).length} events`;

  updateApprovalCard(inc, d.timeline || [], token);
}

/**
 * The verdict block — the single most important thing this UI renders.
 *
 * Both probes are shown side by side, always, even when they agree. Showing
 * only the sustained result would hide the control group, and the control
 * group is the entire argument: "sustained checking beats naive checking" is
 * a comparison, and a comparison needs both numbers on screen.
 */
function renderVerdicts(verdicts) {
  if (!verdicts.length) { $("verdict-panel").hidden = true; return; }
  $("verdict-panel").hidden = false;

  $("verdicts").innerHTML = verdicts.map((v) => {
    const disagreed = v.naive_recovered && !v.sustained_recovered;
    const probe = (kind, passed, detail) => `
      <div class="probe ${passed ? "p-pass" : "p-fail"}">
        <div class="probe-k">${esc(kind)}</div>
        <div class="probe-v">${passed ? "recovered" : "not recovered"}</div>
        <div class="probe-d">${esc(detail)}</div>
      </div>`;

    return `<div class="verdict">
      <div class="verdict-top">
        <span class="badge ${v.sustained_recovered ? "b-good" : "b-bad"}">
          ${v.sustained_recovered ? "verdict: recovered" : "verdict: not recovered"}</span>
        <span class="verdict-attempt">attempt ${esc(v.attempt)}</span>
      </div>
      <div class="verdict-pair">
        ${probe("naive probe (control)", v.naive_recovered, "1 probe · idle · no load")}
        ${probe("sustained probe (oracle)", v.sustained_recovered,
                `${esc(v.window_s)}s window · ~${esc(v.load_rps)} rps`)}
      </div>
      ${disagreed ? `<div class="disagree">
        <strong>The two probes disagreed.</strong> A one-shot check on an idle service said this
        was fixed; holding ~${esc(v.load_rps)} rps for ${esc(v.window_s)}s showed it was not.
        That is one false positive a conventional self-healing agent would have shipped as a success.
      </div>` : ""}
    </div>`;
  }).join("");
}

/* Timeline renderers. One entry per event kind.
   🔴 Every interpolation is escaped — see invariant (a). The fields that look
      safely numeric (confidence, rrf score, line counts) come out of a JSONB
      payload originally written from model output. */
const KIND_RENDER = {
  state_change: (p) =>
    `state → <b>${esc(p.to)}</b> <span class="muted">${esc(p.why || "")}</span>`,

  logs: (p) =>
    `collected logs — kept <b>${esc(p.kept_line_count)}</b> of ${esc(p.raw_line_count)} lines` +
    (p.injection_suspected
      ? ` <span class="badge b-bad">injection suspected</span>` : ""),

  diagnose: (p) =>
    `diagnosis: <b>${esc(p.summary)}</b>
     <span class="muted">${esc(p.suspected_service)} · confidence ${esc(p.confidence)}</span>`,

  retrieve: (p) =>
    `retrieved <b>${esc(p.kept)}</b> runbooks — top <code class="inline">${esc(p.top_runbook_id)}</code>
     <span class="muted">rrf ${esc(p.top_rrf_score)}</span>`,

  propose: (p) =>
    `proposal: <code class="inline">${esc(p.action)}</code>
     <span class="muted">${esc(JSON.stringify(p.params))}</span>`,

  approval: (p) => `human decision: <b>${esc(p.decision)}</b>`,

  execute: (p) =>
    p.phase === "rejected"
      ? `broker rejected at layer ${esc(p.layer)} — ${esc(p.reason)}`
      : p.phase === "result"
        ? `action finished: exit ${esc(p.exit_code)} — ${esc(p.detail)}`
        : `executing <code class="inline">${esc(p.action)}</code>…`,

  verify: (p) =>
    p.recheck_5min_ok !== undefined
      ? `5-minute recheck: ${p.recheck_5min_ok ? "still healthy" : "<b>FAILED</b> — the 30s window was too short"}`
      : `oracle: sustained <b>${esc(p.sustained_recovered)}</b>, naive ${esc(p.naive_recovered)}` +
        (p.disagreed ? ` <b>— they disagreed</b>` : ""),

  escalate: (p) => `escalated (${esc(p.reason)}) — ${esc(p.detail || "")}`,
};

function kindClass(kind, p) {
  if (kind === "escalate" || (kind === "execute" && p && p.phase === "rejected")) return "k-error";
  if (kind === "verify" && p && p.sustained_recovered === true) return "k-ok";
  if (kind === "verify" && p && p.sustained_recovered === false) return "k-error";
  if (kind === "retrieve") return "k-know";
  if (kind === "approval" || kind === "propose") return "k-wait";
  return "";
}

/**
 * Append one timeline event.
 *
 * The timeline is APPEND-ONLY: rows are added, never mutated. Reconciliation —
 * React's whole value proposition — solves a problem this surface does not
 * have, because an event that has happened does not change.
 *
 * 🔴 Unknown kinds are LOUD in the console. A silently blank row would let the
 *    backend add an event type and the UI quietly drop it forever. This is the
 *    runtime stand-in for TypeScript's exhaustiveness check (D14).
 */
function appendEvent(ev) {
  // Dedupe by event id. The server resumes from Last-Event-ID now, so a
  // reconnect should not replay — this guarantees it even if that regresses.
  if (ev.id != null) {
    if (state.seenEvents.has(ev.id)) return;
    state.seenEvents.add(ev.id);
  }

  const payload = typeof ev.payload === "string" ? JSON.parse(ev.payload) : ev.payload;
  const renderer = KIND_RENDER[ev.kind];
  if (!renderer) { console.error("unknown event kind:", ev.kind, payload); return; }

  const li = document.createElement("li");
  li.className = `tl-item ${kindClass(ev.kind, payload)}`;
  li.innerHTML =
    `<span class="tl-time">${esc(fmtTime(ev.at))}</span>` +
    `<span class="tl-dot" aria-hidden="true"></span>` +
    `<span class="tl-body">${renderer(payload || {})}</span>`;
  $("timeline").appendChild(li);
}

function openStream(id, token) {
  closeStream();
  const es = new EventSource(`/api/incidents/${id}/stream`);
  state.stream = es;

  es.addEventListener("event", (e) => {
    if (!isCurrent(token)) return;
    appendEvent(JSON.parse(e.data));
    $("timeline-note").textContent = `${state.seenEvents.size} events`;
  });

  // The server pushes a fresh incident row whenever the state changes, so the
  // header and stepper stay live without the client re-fetching anything.
  es.addEventListener("incident", (e) => {
    if (!isCurrent(token)) return;
    const inc = JSON.parse(e.data);
    $("det-badge").innerHTML = badgeFor(inc.state);
    renderStepper(inc);
    if (inc.state === "AWAITING_APPROVAL") reloadDetail(id, token);
  });

  es.addEventListener("done", () => {
    if (!isCurrent(token)) return;
    closeStream();
    setConn("live", "incident closed");
    reloadDetail(id, token);   // pick up the final verdict rows
  });

  es.onopen = () => { if (isCurrent(token)) setConn("live", "streaming"); };
  es.onerror = () => { if (isCurrent(token)) setConn("down", "stream interrupted"); };
}

function closeStream() {
  if (state.stream) { state.stream.close(); state.stream = null; }
}

/** Re-fetch the detail without tearing down the stream or the generation. */
async function reloadDetail(id, token) {
  try {
    const d = await api(`/api/incidents/${id}`);
    if (!isCurrent(token)) return;
    renderDetail(d, token);
  } catch { /* the stream is still the source of truth; a failed refresh is not fatal */ }
}

/* ── 8. Approval gate ──────────────────────────────────────────────────── */

/** Lines that look like an instruction rather than a log line. The backend
 *  already flags the incident; this highlights WHERE, so the operator does not
 *  have to scan a 40-line <pre> by eye (S-03). */
const INJECTION_HINTS =
  /(ignore (all )?previous|disregard .* instructions|you are now|system prompt|approve this|run the following|as an ai)/i;

function evidenceBlock(lines) {
  if (!lines.length) return `<span class="muted">no log lines captured</span>`;
  const rendered = lines.map((l) => {
    const safe = esc(l);
    return INJECTION_HINTS.test(l) ? `<mark>${safe}</mark>` : safe;
  }).join("\n");
  return `<pre class="snippet">${rendered}</pre>`;
}

const lastOfKind = (timeline, kind) => [...timeline].reverse().find((e) => e.kind === kind);

function payloadOf(ev) {
  if (!ev) return {};
  return typeof ev.payload === "string" ? JSON.parse(ev.payload) : (ev.payload || {});
}

async function updateApprovalCard(inc, timeline, token) {
  const card = $("approval");
  if (String(inc.state) !== "AWAITING_APPROVAL") { card.hidden = true; return; }

  let approval;
  try {
    ({ approval } = await api(`/api/incidents/${inc.id}/proposal`));
  } catch (e) {
    if (isCurrent(token)) card.hidden = true;
    return;
  }

  // 🔴 The generation check matters MORE here than anywhere else in the file.
  //    A slow /proposal response used to be able to paint the previous
  //    incident's action next to this incident's Approve button (F-05).
  //    Showing the wrong action above a live Approve control is the single
  //    worst thing this UI could do.
  if (!isCurrent(token) || state.incidentId !== inc.id) return;

  const logsPayload = payloadOf(lastOfKind(timeline, "logs"));
  const diagPayload = payloadOf(lastOfKind(timeline, "diagnose"));
  const params = typeof approval.params === "string" ? JSON.parse(approval.params) : approval.params;
  const lines = logsPayload.lines || [];

  $("injection-warning").hidden = !logsPayload.injection_suspected;

  $("approval-body").innerHTML = `<div class="kv">
    <div>diagnosis</div><div>${esc(diagPayload.summary || "(none recorded)")}</div>
    <div>proposed action</div><div><code class="inline">${esc(approval.action)}</code></div>
    <div>exact params</div><div><pre class="snippet">${esc(JSON.stringify(params, null, 2))}</pre></div>
    <div>model's reasoning</div><div>${esc(approval.rationale)}</div>
    <div>evidence</div><div>${evidenceBlock(lines)}</div>
  </div>`;

  $("reject-form").hidden = true;
  $("approval-msg").textContent = "";
  card.hidden = false;
}

$("btn-approve").addEventListener("click", async () => {
  const ok = await confirmAction(
    "Approve and execute?",
    "The agent will run the proposed action against a live container, then the oracle will hold "
    + "load on the service for 30 seconds to decide whether it actually worked.",
    "Approve & execute");
  if (ok) decide("approve");
});

$("btn-reject").addEventListener("click", () => {
  $("reject-form").hidden = false;
  $("reject-reason").focus();
});
$("btn-reject-cancel").addEventListener("click", () => { $("reject-form").hidden = true; });

$("reject-form").addEventListener("submit", (e) => {
  e.preventDefault();
  decide("reject", { reason: $("reject-reason").value.trim() });
});

/** 🔴 The two endpoints, written out.
 *
 *  This used to be `/api/incidents/${id}/${action}` with `action` interpolated.
 *  It worked, and it made the client/server contract unverifiable: a build-time
 *  check cannot tell which routes `${action}` reaches, so nothing could catch a
 *  renamed endpoint (`tests/test_frontend_contract.py`). Two literal strings are
 *  also greppable — you can find every caller of /approve from the shell. */
const DECISION_ENDPOINT = {
  approve: (id) => `/api/incidents/${id}/approve`,
  reject:  (id) => `/api/incidents/${id}/reject`,
};

async function decide(action, body) {
  const id = state.incidentId;
  const buttons = [$("btn-approve"), $("btn-reject")];
  buttons.forEach((b) => { b.disabled = true; });
  $("approval-msg").textContent = "submitting…";

  try {
    const out = await api(DECISION_ENDPOINT[action](id), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    $("approval-msg").textContent =
      out.status === "already_decided" ? "already decided (idempotent)" : "recorded";
    toast(out.status === "already_decided"
      ? "This incident was already decided."
      : `Incident #${id} ${action}ed.`, "t-good");
    showView("detail", id);
  } catch (e) {
    $("approval-msg").textContent = `failed: ${e.message}`;
    toast(`Could not ${action}: ${e.message}`, "t-bad");
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
  }
}

/* ── 9. Evidence ───────────────────────────────────────────────────────── */

/** A rate drawn together with its confidence interval.
 *  🔴 They are one object on purpose. METRICS.md §1's G3 gate says no number
 *  claims anything without an interval; making them separate table columns is
 *  how they end up separated (and the old UI's header/cell mismatch, F-04, is
 *  what that looks like when it goes wrong). */
function ciCell(r) {
  if (r.rate_pct == null) return `<span class="muted">no data</span>`;
  const lo = Number(r.ci_low_pct), hi = Number(r.ci_high_pct), v = Number(r.rate_pct);
  return `<div class="ci">
    <span class="ci-text">${esc(v)}%</span>
    <span class="ci-track">
      <span class="ci-rail"></span>
      <span class="ci-band" style="left:${lo}%;width:${Math.max(hi - lo, 1)}%"></span>
      <span class="ci-dot" style="left:${v}%"></span>
    </span>
    <span class="ci-text muted">${esc(lo)}–${esc(hi)}</span>
    ${r.underpowered ? `<span class="underpowered" title="too few runs to conclude anything">⚠ underpowered</span>` : ""}
  </div>`;
}

/** One metric block: id, name, definition, table.
 *  `cols` is a list of [header, cellFn] pairs — headers and cells are defined
 *  together so they cannot drift apart, which is precisely how F-04 happened. */
function metricBlock(id, name, definition, rows, cols) {
  const body = rows.length
    ? rows.map((r) => `<tr>${cols.map(([, cell]) => `<td class="num">${cell(r)}</td>`).join("")}</tr>`).join("")
    : `<tr><td colspan="${cols.length}" class="muted" style="text-align:center;padding:22px">no data yet</td></tr>`;
  return `<div class="panel metric">
    <div class="metric-head">
      <span class="metric-id">${esc(id)}</span>
      <span class="metric-name">${esc(name)}</span>
      <span class="metric-def">${esc(definition)}</span>
    </div>
    <div class="table-wrap"><table>
      <thead><tr>${cols.map(([h]) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
      <tbody>${body}</tbody>
    </table></div>
  </div>`;
}

const deltaCell = (v) => {
  if (v == null) return `<span class="muted">no paired arm</span>`;
  const n = Number(v);
  const cls = n > 0 ? "delta-pos" : n < 0 ? "delta-neg" : "delta-zero";
  return `<span class="${cls}">${n > 0 ? "+" : ""}${esc(n)} pp</span>`;
};

async function loadEvidence(token) {
  $("evidence-body").innerHTML = emptyState("Loading metrics…", "");
  try {
    const s = await api("/api/stats");
    if (!isCurrent(token)) return;
    hideBanner();
    setConn("live", "connected");

    $("evidence-body").innerHTML = [
      metricBlock("N1", "Auto-fix rate", "per fault class × RAG arm · Wilson 95% interval",
        s.autofix, [
          ["fault", (r) => esc(r.fault_class)],
          ["rag", (r) => (r.rag_enabled ? "on" : "off")],
          ["n", (r) => esc(r.n)],
          ["rate with 95% interval", ciCell],
        ]),

      metricBlock("N1b", "Naive vs sustained disagreements",
        "false positives the sustained oracle caught",
        s.disagreements || [], [
          ["fault", (r) => esc(r.fault_class)],
          ["verdicts", (r) => esc(r.verdicts)],
          ["naive said fixed", (r) => esc(r.naive_pass)],
          ["sustained said fixed", (r) => esc(r.sustained_pass)],
          ["disagreements", (r) => Number(r.disagreements) > 0
            ? `<span class="delta-neg">${esc(r.disagreements)}</span>` : esc(r.disagreements)],
        ]),

      metricBlock("N3", "Retrieval ablation", "same seeds, RAG on vs off",
        s.ablation_delta, [
          ["fault", (r) => esc(r.fault_class)],
          ["with rag", (r) => pct(r.with_rag_pct)],
          ["n", (r) => esc(r.n_with_rag)],
          ["without rag", (r) => pct(r.no_rag_pct)],
          ["n", (r) => esc(r.n_no_rag)],
          ["delta", (r) => deltaCell(r.delta_pp)],
        ]),

      metricBlock("N4", "Retrieval hit rate", "per (incident, attempt) — random baseline ≈ 2-4%",
        s.retrieval_hits, [
          ["fault", (r) => esc(r.fault_class)],
          ["attempts", (r) => esc(r.n)],
          ["hit@1", (r) => pct(r.hit_at_1_pct)],
          ["hit@3", (r) => pct(r.hit_at_3_pct)],
        ]),

      metricBlock("N7", "Escalations by cause", "a single escalation number would hide the interesting split",
        s.escalations, [
          ["fault", (r) => esc(r.fault_class)],
          ["voluntary", (r) => esc(r.voluntary)],
          ["exhausted", (r) => esc(r.exhausted)],
          ["you rejected", (r) => esc(r.human_rejected)],
          ["broker blocked", (r) => esc(r.broker_rejected)],
          ["infra", (r) => esc(r.infra)],
          ["total", (r) => esc(r.n)],
        ]),

      metricBlock("N2", "Time to recovery", "detected → closed",
        s.mttr, [
          ["fault", (r) => esc(r.fault_class)],
          ["n", (r) => esc(r.n)],
          ["mean", (r) => esc(fmtDuration(r.mean_s))],
          ["p50", (r) => esc(fmtDuration(r.p50_s))],
          ["p95", (r) => esc(fmtDuration(r.p95_s))],
        ]),

      metricBlock("N6", "Tokens per incident", "N6 · cost of one incident",
        s.tokens, [
          ["fault", (r) => esc(r.fault_class)],
          ["n", (r) => esc(r.n)],
          ["avg", (r) => esc(r.avg_tokens)],
          ["max", (r) => esc(r.max_tokens)],
        ]),

      metricBlock("N5", "Human rejection rate", "your judgement, not the oracle's",
        s.rejection, [
          ["fault", (r) => esc(r.fault_class)],
          ["proposals", (r) => esc(r.proposals)],
          ["rejected", (r) => esc(r.rejected)],
          ["rate", (r) => (r.proposals
            ? `${Math.round((100 * r.rejected) / r.proposals)}%` : "—")],
        ]),
    ].join("");
  } catch (e) {
    if (!isCurrent(token)) return;
    setConn("down", "no connection");
    $("evidence-body").innerHTML = emptyState("Could not load metrics", e.message);
  }
}

/* ── 10. Chaos lab ─────────────────────────────────────────────────────── */

async function loadChaos(token) {
  try {
    const faults = await api("/api/faults");
    if (!isCurrent(token)) return;
    hideBanner();
    setConn("live", "connected");

    $("faults").innerHTML = faults.map((f) => `
      <div class="fault">
        <div class="fault-top">
          <span class="fault-id">${esc(f.id)}</span>
          <span class="fault-title">${esc(f.title)}</span>
        </div>
        <div class="fault-symptom">${esc(f.symptom)}</div>
        <div class="fault-tags">
          ${f.restart_alone_fixes
            ? `<span class="tag">restart fixes it</span>`
            : `<span class="tag t-hot">restart will NOT fix it</span>`}
          ${f.load_dependent ? `<span class="tag t-hot">load-dependent</span>` : ""}
          ${(f.touches || []).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}
        </div>
        <div class="fault-actions">
          <button class="btn btn-inject" data-inject="${esc(f.id)}">Inject ${esc(f.id)}</button>
          <button class="btn btn-heal" data-heal="${esc(f.id)}">Heal</button>
        </div>
      </div>`).join("");

    $("faults").querySelectorAll("[data-inject]").forEach((b) =>
      b.addEventListener("click", () => injectFault(b.dataset.inject)));
    // 🔴 Per-fault heal. POST /api/faults/{id}/heal already existed and had no
    //    caller in the UI — you could break one thing but only fix all of them (F-10).
    $("faults").querySelectorAll("[data-heal]").forEach((b) =>
      b.addEventListener("click", () => healFault(b.dataset.heal)));
  } catch (e) {
    if (!isCurrent(token)) return;
    setConn("down", "no connection");
    $("faults").innerHTML = emptyState("Could not load fault list", e.message);
  }
}

async function injectFault(fid) {
  const ok = await confirmAction(
    `Inject ${fid}?`,
    "This really breaks the victim stack and opens an incident. It is reversible with Heal, "
    + "but any incident already in flight will be affected.",
    `Inject ${fid}`);
  if (!ok) return;

  toast(`Injecting ${fid}…`);
  try {
    const out = await api(`/api/faults/${fid}/inject?write=true`, { method: "POST" });
    if (out.incident_id) {
      toast(`${fid} injected — incident #${out.incident_id}`, "t-bad");
      showView("detail", out.incident_id);
    } else {
      toast(`${fid}: dry run, nothing was written`);
    }
  } catch (e) {
    toast(`Inject failed: ${e.message}`, "t-bad");
    showBanner(`Could not inject ${fid} — ${e.message}`);
  }
}

async function healFault(fid) {
  toast(`Healing ${fid}…`);
  try {
    const out = await api(`/api/faults/${fid}/heal`, { method: "POST" });
    toast(`${fid} healed — ${out.detail || "done"}`, "t-good");
    if (state.view === "overview") loadOverview(nextGen());
  } catch (e) {
    toast(`Heal failed: ${e.message}`, "t-bad");
  }
}

$("btn-heal-all").addEventListener("click", async () => {
  const ok = await confirmAction(
    "Reset the world?",
    "Every injected fault is reverted and the victim stack goes back to a clean slate. "
    + "Incidents already recorded are kept.",
    "Heal everything");
  if (!ok) return;

  try {
    const out = await api("/api/faults/heal-all", { method: "POST" });
    const n = Array.isArray(out.lines) ? out.lines.length : 0;
    toast(`World reset — ${n} heal step${n === 1 ? "" : "s"} ran`, "t-good");
  } catch (e) {
    toast(`Heal-all failed: ${e.message}`, "t-bad");
    showBanner(`Could not reset — ${e.message}`);
  }
});

/* ── 11. Boot + polling ────────────────────────────────────────────────── */

setInterval(() => {
  $("clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}, 1000);

/**
 * Background refresh for the list views.
 *
 * 🔴 Two guards, both of which the old version was missing:
 *
 *   1. `document.hidden` — a backgrounded tab used to keep polling forever.
 *      /api/services makes real Docker calls, so an abandoned tab was a
 *      permanent load on the daemon it is supposed to be observing.
 *
 *   2. The detail view is excluded — it has SSE, which is already live. Polling
 *      on top of a stream is duplicate work that only adds ways to disagree
 *      with yourself.
 */
const POLL_MS = 5000;
setInterval(() => {
  if (document.hidden) return;
  if (state.view === "overview") loadOverview(state.gen);
  else if (state.view === "incidents") loadIncidents(state.gen);
}, POLL_MS);

// Refresh immediately when the tab comes back, rather than making the operator
// look at up-to-five-seconds-stale data on a screen they just returned to.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && (state.view === "overview" || state.view === "incidents")) {
    showView(state.view);
  }
});

showView("overview");
