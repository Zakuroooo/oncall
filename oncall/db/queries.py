"""oncall/db/queries.py — poore project ka SAARA SQL, ek jagah.

Concepts pehle padho: `docs/TEACH_PHASE4.md` §1–§8. Ye file un concepts ka
implementation hai, unka explanation nahi.

═══════════════════════════════════════════════════════════════════════════════
Is file ke teen niyam (aur teeno enforce-able hain)
═══════════════════════════════════════════════════════════════════════════════

1. 🔒 **Har parameter `%s` se jaata hai.** Is file mein `cur.execute()` ka
   pehla argument **hamesha** ya ek literal string hai, ya ek module-level
   `_SQL_*` constant ka naam. Kabhi f-string, `%`, ya `.format()` nahi.

   Check (AST, grep **nahi**):
   `python3 tests/test_queries.py` → `test_execute_first_arg_is_literal_or_name`.

   🔴 Ye niyam pehle draft mein **khud tut gaya tha**: mere paas
   `cur.execute(f"SELECT ...")` likha tha. Wo *safe* tha (interpolate ho rahi
   cheez ek module constant thi, user input nahi) — lekin **check karne layak
   nahi tha**, aur ek niyam jiska verification apni hi file pe fail ho jaaye,
   wo niyam nahi ek ummid hai. Isliye saare query strings module level pe
   constants ban gaye: interpolation ek baar, import time pe hoti hai.

   🔴 Phir doosri galti: maine check ke liye ek **grep** likha tha
   (`execute\\((f"|f.)`). Wo grep bhi fail hua — is docstring ke upar wale
   paragraph pe, jo bad pattern ko *quote* karta hai. Grep "mention" aur
   "actual code" mein farq nahi kar sakta. Isliye check ab **AST-based** hai:
   `ast.walk()` sirf asli `Call` nodes dekhta hai, comments aur strings ko nahi.
   Seekh (`CLAUDE.md` §2, aur is project mein **chauthi baar**): *ek rule ko is
   tarah likho ki uska verification command chal sake — aur agar rule apni hi
   documentation mein quote hota hai, toh wo verification regex nahi, parser
   hona chahiye.*

2. 🔴 **`SELECT *` kahin nahi.** Har SELECT explicit column list likhta hai.
   Ye style nahi, **security control** hai: `runbooks` ka ek column N4 metric ki
   ground truth hai aur `CLAUDE.md` §3 kehta hai wo agent ko dikhni nahi
   chahiye. Us column ka **naam** is tree mein kahin likha nahi hai (`make lint`
   check 4 grep karta hai) — lekin star-select usko **bina naam liye** le aata
   hai. Authoritative list `CLAUDE.md` §3 aur `Makefile` ke `lint` target mein
   hai; yahan sirf pointer.

   Check (AST, aur ye check **poore `oncall/`** pe lagta hai, sirf is file pe
   nahi): `python3 tests/test_schema.py` → `test_no_select_star_under_oncall`.
   Wo bhi pehle regex tha aur is file ke docstring pe hi fail hua tha — wahi
   "mention vs code" kahani, doosri baar.

3. 🔴 **`chaos/` se koi import nahi.** Answer-key fields is process ke andar
   aane hi nahi chahiye (`CLAUDE.md` §3). Check:
   `python3 tests/test_queries.py` → `test_no_chaos_import`.

═══════════════════════════════════════════════════════════════════════════════
Naam ka pattern — kyun `advance_state` aur `close_incident` do alag function
═══════════════════════════════════════════════════════════════════════════════
`init.sql:66` ka CHECK terminal state (`RECOVERED`/`ESCALATED`) ke saath
`closed_at NOT NULL` maangta hai. Ek generic `set_state(state, closed_at=None)`
banane ka matlab hota: galti karna possible. Do function ka matlab: galti
**impossible** — `advance_state()` terminal state bhejne pe khud `ValueError`
deta hai, aur `close_incident()` `closed_at` khud bharta hai.
Detail: `docs/TEACH_PHASE4.md` §7.

═══════════════════════════════════════════════════════════════════════════════
psycopg optional kyun hai
═══════════════════════════════════════════════════════════════════════════════
`import oncall.db.queries` bina psycopg ke bhi chalta hai — bas koi function
call karne pe `RuntimeError` aata hai (`pool.get_pool()` se). Wajah wahi hai jo
`pool.py` mein thi: text-only tests (`tests/test_schema.py`,
`tests/test_no_shell.py`) is file ko **padhte** hain, chalate nahi. Ek
import-time `ModuleNotFoundError` un teston ko bina kaaran tod deta.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from oncall.config import MAX_ATTEMPTS
from oncall.db.pool import get_conn

# psycopg ka jsonb adapter. Kyun `json.dumps` nahi → TEACH_PHASE4 §8
# (double-encoding read time pe pakdi jaati hai, write time pe nahi).
try:
    from psycopg.types.json import Jsonb  # type: ignore[import-not-found]

    _HAS_JSONB = True
except ModuleNotFoundError:  # pragma: no cover - install state pe depend karta hai
    Jsonb = None  # type: ignore[assignment]
    _HAS_JSONB = False


# ═════════════════════════════════════════════════════════════════════════════
# 0. Constants — schema se COPY, aur wo copy jaan-boojh kar hai
# ═════════════════════════════════════════════════════════════════════════════
#
# 🔴 `stack/oncalldb/init.sql` in values ka **maalik** hai (D50 — ek schema
#    file, do runner). Yahan wahi list dobara likhi hai. Duplicate kyun accept
#    kiya:
#
#    Bina in constants ke, `advance_state(iid, to="RETRIEVEING")` (typo) ek
#    round trip leta, Postgres `IntegrityError` deta, aur wo error stack trace
#    ke andar dikhta — chalti hui service mein, 3 baje raat ko. In constants ke
#    saath wo `ValueError` ban jaata hai **process ke andar**, DB touch karne se
#    pehle, ek saaf message ke saath.
#
#    Drift ka khatra asli hai, aur uska jawab ek test hai, ek comment nahi:
#    `tests/test_queries.py::test_states_match_init_sql` init.sql ko parse karta
#    hai aur in do list ko compare karta hai. Agar koi ek jagah state add kare
#    aur doosri bhool jaaye, wo test lal ho jaata hai.

#: State machine ke 10 nodes. `init.sql:60` ka CHECK inhi ko allow karta hai.
STATES: tuple[str, ...] = (
    "DETECTED",
    "COLLECTING",
    "DIAGNOSING",
    "RETRIEVING",
    "PROPOSING",
    "AWAITING_APPROVAL",
    "EXECUTING",
    "VERIFYING",
    "RECOVERED",
    "ESCALATED",
)

#: 🔴 Ye do state `closed_at` ko compulsory bana dete hain (`init.sql:66`).
TERMINAL_STATES: tuple[str, ...] = ("RECOVERED", "ESCALATED")

#: `events.kind` ka CHECK (`init.sql:104`).
EVENT_KINDS: tuple[str, ...] = (
    "health_check",
    "logs",
    "diagnose",
    "retrieve",
    "propose",
    "approval",
    "execute",
    "verify",
    "escalate",
    "state_change",
)

#: 🔴 `runbooks` ka safe column list. Do column yahan JAAN-BOOJH KAR nahi hain:
#: (a) N4 ka answer-key column — wo ground truth hai, leak hoga; (b) `embedding`
#: — ~1.5 KB per row, aur retrieval ke baad kisi ko chahiye nahi hota.
#: (a) ka **naam** is tree mein likha nahi hai; authoritative list `CLAUDE.md`
#: §3 aur `Makefile` ke `lint` target mein hai.
#: Phase 6 ka `retrieval/search.py` isi constant se apna SELECT banata hai,
#: apna column list nahi likhta — taaki leak guard **ek** jagah rahe.
RUNBOOK_COLUMNS: tuple[str, ...] = (
    "id",
    "title",
    "body",
    "source",
    "source_url",
    "licence",
    "fetched_at",
)

#: Ek `running` job kitni der baad "atki hui" maani jaaye. Reaper isko use
#: karta hai.
#:
#: ⚠️ 600 s ek **estimate** hai, measurement nahi (2026-08-22).
#: Tark: sabse lambi legit job = collect (~5 s) + 2 LLM call (~10 s) +
#: retrieval (~1 s) + execute (~5 s) + 🔥 oracle ka 30 s window = ~51 s.
#: 600 s us se ~12× hai — matlab ek slow-but-alive job ko reaper galti se
#: nahi maarega. Bahut chhota rakhna (jaise 60 s) ka matlab hota: oracle ke
#: chalte-chalte job wapas queue mein daal di jaaye, aur **do** action ek hi
#: incident pe chalein. Wo double-execution bug hai.
#: Tripwire: agar logs mein "reaped" dikhe jabki job asli mein chal rahi thi,
#: ye number chhota hai.
JOB_STALE_AFTER_S = 600


# ═════════════════════════════════════════════════════════════════════════════
# 1. Chhote helpers — validation process ke andar, DB se pehle
# ═════════════════════════════════════════════════════════════════════════════


def _jsonb(payload: Mapping[str, Any] | Sequence[Any]) -> Any:
    """Python dict/list ko psycopg ke `Jsonb` mein lapeto."""
    if not _HAS_JSONB:  # pragma: no cover - install state
        raise RuntimeError(
            "psycopg install nahi hai — `make install` chalao. "
            "(jsonb column likhne ke liye psycopg.types.json.Jsonb chahiye.)"
        )
    return Jsonb(payload)


def _check_state(state: str) -> str:
    """Ek state naam validate karo. Typo yahan marta hai, DB pe nahi."""
    if state not in STATES:
        raise ValueError(
            f"'{state}' ek valid state nahi hai. Allowed: {', '.join(STATES)}. "
            "(Maalik `stack/oncalldb/init.sql` ka CHECK hai; ye list uski copy hai.)"
        )
    return state


def _check_kind(kind: str) -> str:
    """Ek event kind validate karo."""
    if kind not in EVENT_KINDS:
        raise ValueError(
            f"'{kind}' ek valid event kind nahi hai. Allowed: {', '.join(EVENT_KINDS)}."
        )
    return kind


# `events` ka safe column list — timeline read ke liye.
_EVENT_COLUMNS = "id, incident_id, at, kind, payload"

# `incidents` ka safe column list.
#
# 🔴 `fault_class` yahan HAI aur wo safe hai; fault ka expected-action field
#    `incidents` schema mein hi nahi hai (`init.sql:29–42` ka comment iska poora
#    tark deta hai). Yaani yahan leak ka koi rasta nahi — na `SELECT *` se,
#    na explicit list se.
_INCIDENT_COLUMNS = (
    "id, service, fault_class, state, attempt, "
    "detected_at, closed_at, rag_enabled, seed"
)

# ── Query strings, module level ──────────────────────────────────────────────
#
# 🔴 Interpolation SIRF yahan hoti hai — ek baar, import time pe, aur sirf
#    module constants ke saath. `cur.execute()` ke andar kabhi nahi.
#    Faayda: `execute()` ka pehla argument hamesha ek naam ya literal hai,
#    matlab "kya kahin f-string hai?" ek grep se jawab de deta hai.

_SQL_INSERT_INCIDENT = (
    "INSERT INTO incidents (service, fault_class, state, attempt, rag_enabled, seed) "
    "VALUES (%s, %s, %s, 0, %s, %s) RETURNING id"
)
_SQL_INSERT_EVENT = (
    "INSERT INTO events (incident_id, kind, payload) VALUES (%s, %s, %s) RETURNING id"
)
_SQL_INSERT_JOB = "INSERT INTO jobs (incident_id, status) VALUES (%s, 'queued') RETURNING id"

_SQL_GET_INCIDENT = f"SELECT {_INCIDENT_COLUMNS} FROM incidents WHERE id = %s"
_SQL_LIST_INCIDENTS = (
    f"SELECT {_INCIDENT_COLUMNS} FROM incidents ORDER BY detected_at DESC LIMIT %s"
)
_SQL_LIST_OPEN_INCIDENTS = (
    f"SELECT {_INCIDENT_COLUMNS} FROM incidents "
    "WHERE state NOT IN ('RECOVERED', 'ESCALATED') "
    "ORDER BY detected_at DESC LIMIT %s"
)
_SQL_GET_TIMELINE = (
    f"SELECT {_EVENT_COLUMNS} FROM events WHERE incident_id = %s ORDER BY at, id LIMIT %s"
)


# ═════════════════════════════════════════════════════════════════════════════
# 2. incidents — banao, padho, state badlo
# ═════════════════════════════════════════════════════════════════════════════


def open_incident(
    *,
    service: str,
    fault_class: str,
    seed: int,
    rag_enabled: bool,
) -> int:
    """🔥 Naya incident + uska pehla event + uska pehla job — EK transaction mein.

    Ye function `queries.py` ka sabse important design point hai, aur
    `docs/TEACH_PHASE4.md` §4 uska poora tark deta hai. Chhota version:

    Teen row likhni hain. Agar ye teen alag transaction hote aur process
    doosre ke baad mar jaata, toh incident **exist karta lekin uska job nahi**
    — aur wo incident hamesha ke liye `DETECTED` pe latak jaata, bina kisi
    error ke. Us bug ka koi log nahi hota, koi exception nahi. Ek `with
    get_conn()` block usko impossible bana deta hai: ya teeno rows, ya ek bhi
    nahi.

    🔴 Isliye ye function connection **khud** kholta hai, caller se leta nahi.
       Agar caller connection deta, toh transaction boundary caller ke haath
       mein chali jaati aur "atomic" ka vaada ye function nibha hi nahi sakta.

    Returns:
        Naye incident ka id.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # `RETURNING id` — ek round trip. `max(id)` ek asli race hai
            # (TEACH_PHASE4 §3).
            cur.execute(
                _SQL_INSERT_INCIDENT,
                (service, fault_class, "DETECTED", rag_enabled, seed),
            )
            incident_id = int(cur.fetchone()["id"])

            # Row 2: timeline ka pehla entry. UI isi se render hoti hai.
            cur.execute(
                _SQL_INSERT_EVENT,
                (
                    incident_id,
                    "state_change",
                    _jsonb({"from": None, "to": "DETECTED", "why": "fault injected"}),
                ),
            )

            # Row 3: job, taaki worker isko uthaye.
            cur.execute(_SQL_INSERT_JOB, (incident_id,))
    return incident_id


def get_incident(incident_id: int) -> dict[str, Any] | None:
    """Ek incident, ya `None` agar nahi mila."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Explicit column list — `SELECT *` nahi (file ka niyam #2).
            cur.execute(_SQL_GET_INCIDENT, (incident_id,))
            return cur.fetchone()


def list_incidents(*, limit: int = 50, open_only: bool = False) -> list[dict[str, Any]]:
    """Incidents ki list, naye pehle.

    Args:
        open_only: `True` pe sirf khule incidents. Ye
            `incidents_open_idx` (partial index, `init.sql:83`) use karta hai.

    🔴 Yahan do alag query constants hain, ek runtime pe joda gaya string nahi.
       `sql += " WHERE ..."` wala style ek f-string ki taraf pehla kadam hota
       hai, aur wahi niyam #1 tudwaata hai. `LIMIT %s` phir bhi parameter hai
       — wo caller ka input hai.
    """
    sql = _SQL_LIST_OPEN_INCIDENTS if open_only else _SQL_LIST_INCIDENTS
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (limit,))
            return list(cur.fetchall())


# ═════════════════════════════════════════════════════════════════════════════
# 3. 🔥 State transitions — optimistic guard ke saath
# ═════════════════════════════════════════════════════════════════════════════
#
# Concept: `docs/TEACH_PHASE4.md` §6. Chhota version: `WHERE id = %s AND
# state = %s` mein doosri condition **expected purana state** hai. Match nahi
# hua toh `rowcount == 0`, aur wo humein batata hai ki koi doosra process is
# incident ko already aage badha chuka hai. Uske bina ek purani jaankari wala
# worker state ko **peeche** likh deta — bina error ke.

_SQL_ADVANCE_STATE = "UPDATE incidents SET state = %s WHERE id = %s AND state = %s"

# 🔴 Terminal ke liye alag statement, kyunki `init.sql:66` ka CHECK
#    `closed_at` ko saath maangta hai (TEACH_PHASE4 §7).
_SQL_CLOSE_INCIDENT = (
    "UPDATE incidents SET state = %s, closed_at = now() WHERE id = %s AND state = %s"
)

_SQL_BUMP_ATTEMPT = (
    "UPDATE incidents SET attempt = attempt + 1 "
    "WHERE id = %s AND attempt < %s RETURNING attempt"
)


def advance_state(incident_id: int, *, frm: str, to: str, why: str = "") -> bool:
    """Non-terminal state change + uska `state_change` event, ek transaction mein.

    Args:
        frm: expected **purana** state. Optimistic guard.
        to: naya state. Terminal state bhejna `ValueError` hai — `close_incident()`
            use karo.
        why: event payload mein jaata hai. Timeline padhne wale insaan ke liye.

    Returns:
        `True` = state badla. `False` = kisi aur ne pehle badal diya
        (`rowcount == 0`) — caller ko **retry nahi** karna chahiye, usko chup-chaap
        haar maan leni chahiye. Doosra process aage nikal gaya hai; dobara koshish
        karne ka matlab do worker ek hi incident pe kaam karna hai.
    """
    _check_state(frm)
    _check_state(to)
    if to in TERMINAL_STATES:
        # 🔴 Ye ek booby trap hai, aur jaan-boojh kar hai. Agar ye check na hota,
        #    `advance_state(iid, frm="VERIFYING", to="RECOVERED")` chal jaata aur
        #    `closed_at` NULL reh jaata — jise database ka CHECK reject karta,
        #    ek `IntegrityError` ke saath jo raat ke 3 baje samajh nahi aata.
        raise ValueError(
            f"'{to}' ek terminal state hai — `close_incident()` use karo, "
            "jo `closed_at` bhi set karta hai (init.sql:66 ka CHECK)."
        )
    return _transition(incident_id, frm=frm, to=to, why=why, sql=_SQL_ADVANCE_STATE)


def close_incident(incident_id: int, *, frm: str, to: str, why: str = "") -> bool:
    """Terminal state (`RECOVERED`/`ESCALATED`) + `closed_at`, ek statement mein.

    `to` sirf terminal ho sakta hai. Baaki behaviour `advance_state()` jaisa.
    """
    _check_state(frm)
    _check_state(to)
    if to not in TERMINAL_STATES:
        raise ValueError(
            f"'{to}' terminal nahi hai — `advance_state()` use karo. "
            f"Terminal sirf: {', '.join(TERMINAL_STATES)}."
        )
    return _transition(incident_id, frm=frm, to=to, why=why, sql=_SQL_CLOSE_INCIDENT)


def _transition(
    incident_id: int, *, frm: str, to: str, why: str, sql: str
) -> bool:
    """Dono public transition functions ka common body.

    🔴 `sql` ek **module constant** hai jo caller (isi file ka) deta hai — user
       input nahi. Do public function isliye alag hain ki unka *contract* alag
       hai (`closed_at`), body alag nahi. Body ek jagah rakhne se
       "state badla lekin event nahi likha" wali galti dono raste pe impossible
       ho jaati hai.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (to, incident_id, frm))
            if cur.rowcount == 0:
                # Koi doosra aage nikal gaya. Event bhi nahi likhna chahiye —
                # warna timeline mein ek transition dikhta jo hua hi nahi.
                # `with` block bina exception ke nikalta hai, toh yahan tak jo
                # kuch hua (kuch nahi) commit ho jaata hai. Wo theek hai.
                return False
            cur.execute(
                _SQL_INSERT_EVENT,
                (
                    incident_id,
                    "state_change",
                    _jsonb({"from": frm, "to": to, "why": why}),
                ),
            )
    return True


def bump_attempt(incident_id: int) -> int | None:
    """Attempt +1. `None` = limit khatam ho gayi (retry nahi ho sakti).

    🔴 `WHERE attempt < %s` ka matlab: limit **database** enforce karta hai,
       Python ka `if` nahi. Do worker ek saath retry karne ki koshish karein,
       toh dono ka `if attempt < 3` sach dikhta — lekin `UPDATE` sirf ek ka
       chalta. Doosre ko `None` milta hai.

       `init.sql:45` pe `CHECK (attempt BETWEEN 0 AND 3)` bhi hai — matlab
       agar ye guard kabhi bypass ho, DB phir bhi 4th attempt reject karta hai.
       Do layer, aur dono zaruri: guard **saaf `None`** deta hai (handle-able),
       CHECK ek **exception** deta hai (last resort).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_BUMP_ATTEMPT, (incident_id, MAX_ATTEMPTS))
            row = cur.fetchone()
    return None if row is None else int(row["attempt"])


# ═════════════════════════════════════════════════════════════════════════════
# 4. events — timeline aur audit trail
# ═════════════════════════════════════════════════════════════════════════════


def record_event(
    incident_id: int, kind: str, payload: Mapping[str, Any] | None = None
) -> int:
    """Ek event likho. Returns naya event id.

    🔴 Ye function kabhi state nahi badalta. Wo `advance_state()` ka kaam hai.
       Do zimmedaari alag rakhne se ek galti impossible ho jaati hai: ek event
       likh dena jo kehta hai "state badla" jabki state badla hi nahi.
    """
    _check_kind(kind)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                _SQL_INSERT_EVENT, (incident_id, kind, _jsonb(dict(payload or {})))
            )
            return int(cur.fetchone()["id"])


def get_timeline(incident_id: int, *, limit: int = 500) -> list[dict[str, Any]]:
    """Ek incident ke saare events, time order mein. UI ka main read.

    `ORDER BY at, id` — sirf `at` se order karna galat hai: do event ek hi
    microsecond mein likhe ja sakte hain (`_transition()` ka UPDATE+INSERT), aur
    tab unka order **undefined** hota hai. `id` tie-breaker hai, aur wo
    monotonic hai kyunki `bigserial` hai.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_GET_TIMELINE, (incident_id, limit))
            return list(cur.fetchall())


# ── SSE ka incremental read ──────────────────────────────────────────────────
#
# 🔴 Poora timeline har poll pe dobara bhejna galat hota: 30 second ke oracle
#    window mein browser ~30 polls karta, aur har poll 500 events ki JSON
#    banata. `after_id` cursor se sirf NAYE events aate hain, aur cursor
#    monotonic hai kyunki `events.id` bigserial hai.
_SQL_GET_TIMELINE_SINCE = (
    f"SELECT {_EVENT_COLUMNS} FROM events "
    "WHERE incident_id = %s AND id > %s ORDER BY at, id LIMIT %s"
)


def get_timeline_since(
    incident_id: int, after_id: int = 0, *, limit: int = 100
) -> list[dict[str, Any]]:
    """Sirf wo events jinke id `after_id` se bade hain. SSE stream ka read."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_GET_TIMELINE_SINCE, (incident_id, after_id, limit))
            return list(cur.fetchall())


# ═════════════════════════════════════════════════════════════════════════════
# 5. llm_calls — D12 (Langfuse ki jagah, ek table)
# ═════════════════════════════════════════════════════════════════════════════

_SQL_INSERT_LLM_CALL = (
    "INSERT INTO llm_calls (incident_id, call_site, attempt, provider, model, "
    "prompt_tokens, completion_tokens, latency_ms, ok, raw_response, error) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id"
)
_SQL_TOKENS_FOR_INCIDENT = (
    "SELECT coalesce(sum(prompt_tokens), 0) AS prompt_tokens, "
    "coalesce(sum(completion_tokens), 0) AS completion_tokens, "
    "count(*) AS calls "
    "FROM llm_calls WHERE incident_id = %s"
)


def record_llm_call(
    *,
    incident_id: int,
    call_site: str,
    attempt: int,
    provider: str,
    model: str,
    ok: bool,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    latency_ms: int | None = None,
    raw_response: str | None = None,
    error: str | None = None,
) -> int:
    """Ek LLM call ka record. **Fail hui call bhi likhi jaati hai** (`ok=False`).

    🔴 Failure record karna optional nahi hai. `TOKEN_BUDGET.md` ka "kitna kharch
       hua" aur R8 ka "kaunsa provider chal raha tha" — dono ke liye fail hui
       call ka data chahiye. Aur fallback chain (Groq→Gemini→OpenRouter) ka poora
       sabooot yahi table hai: teen row, do `ok=false`, ek `ok=true`.

    ⚠️ `call_site` ka CHECK (`init.sql:130`) sirf `'diagnose'`/`'propose'` allow
       karta hai. Yahan Python-side check jaan-boojh kar **nahi** hai — kyunki
       ye value code se aati hai, user se nahi, aur ek naya call site add karna
       ek DB error ke saath rukna hi chahiye (wo "exactly 2 call sites" claim ka
       enforcement hai, TDD §5).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                _SQL_INSERT_LLM_CALL,
                (
                    incident_id,
                    call_site,
                    attempt,
                    provider,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    latency_ms,
                    ok,
                    raw_response,
                    error,
                ),
            )
            return int(cur.fetchone()["id"])


def token_usage(incident_id: int) -> dict[str, Any]:
    """Ek incident ka token kharch. `coalesce` isliye ki 0 calls pe NULL nahi, 0 aaye."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_TOKENS_FOR_INCIDENT, (incident_id,))
            return cur.fetchone() or {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "calls": 0,
            }


# ═════════════════════════════════════════════════════════════════════════════
# 6. 🔒 approvals — human gate. Ek ROW, ek blocked thread nahi.
# ═════════════════════════════════════════════════════════════════════════════
#
# `init.sql:184` ka comment iska poora tark deta hai. Chhota version: agar
# approval ek `input()` ya ek blocked HTTP request hoti, toh worker restart hone
# pe wo incident hamesha ke liye kho jaata. Row-based approval ka matlab: worker
# mar bhi jaaye, incident `AWAITING_APPROVAL` mein baitha rehta hai aur naya
# worker use utha leta hai.

_APPROVAL_COLUMNS = (
    "id, incident_id, attempt, action, params, rationale, "
    "decision, decided_at, decided_by, created_at"
)

_SQL_INSERT_APPROVAL = (
    "INSERT INTO approvals (incident_id, attempt, action, params, rationale) "
    "VALUES (%s, %s, %s, %s, %s) RETURNING id"
)
_SQL_GET_APPROVAL = (
    f"SELECT {_APPROVAL_COLUMNS} FROM approvals WHERE incident_id = %s AND attempt = %s"
)
_SQL_LIST_PENDING_APPROVALS = (
    f"SELECT {_APPROVAL_COLUMNS} FROM approvals "
    "WHERE decision IS NULL ORDER BY created_at LIMIT %s"
)

# 🔒 `AND decision IS NULL` — yahi wo clause hai jo double-decide rokta hai.
_SQL_DECIDE_APPROVAL = (
    "UPDATE approvals SET decision = %s, decided_at = now(), decided_by = %s "
    "WHERE incident_id = %s AND attempt = %s AND decision IS NULL "
    f"RETURNING {_APPROVAL_COLUMNS}"
)


def create_approval(
    *,
    incident_id: int,
    attempt: int,
    action: str,
    params: Mapping[str, Any],
    rationale: str | None = None,
) -> int:
    """Ek pending approval banao. LLM #2 ke propose karne ke baad.

    🔒 `init.sql:186` pe `UNIQUE (incident_id, attempt)` hai. Matlab ek attempt
       pe do proposal likhna **database** reject karta hai. Yahan Python check
       nahi hai kyunki wo race ko rok nahi sakta: do concurrent request ko dono
       ko "koi approval nahi hai" dikhta, aur dono insert karte. Constraint
       rokta hai, `SELECT`-phir-`INSERT` nahi.

    Raises:
        psycopg ka `UniqueViolation` agar is attempt ka approval already hai.
        Caller ko isko **handle** karna chahiye, retry nahi — duplicate ka
        matlab hai "koi doosra already propose kar chuka".
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                _SQL_INSERT_APPROVAL,
                (incident_id, attempt, action, _jsonb(dict(params)), rationale),
            )
            return int(cur.fetchone()["id"])


def get_approval(incident_id: int, attempt: int) -> dict[str, Any] | None:
    """Ek attempt ka approval row (pending ho ya decided), ya `None`."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_GET_APPROVAL, (incident_id, attempt))
            return cur.fetchone()


def list_pending_approvals(*, limit: int = 50) -> list[dict[str, Any]]:
    """Sab pending approvals, purane pehle. UI ka polling read.

    `approvals_pending_idx` (partial index, `init.sql:196`) isko serve karta hai
    — index mein sirf `decision IS NULL` rows hain.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_LIST_PENDING_APPROVALS, (limit,))
            return list(cur.fetchall())


def decide_approval(
    *,
    incident_id: int,
    attempt: int,
    decision: str,
    decided_by: str = "operator",
) -> dict[str, Any] | None:
    """🔒 Approve ya reject. Returns updated row, ya `None` agar kuch nahi hua.

    `None` do wajah se aa sakta hai, aur dono ka matlab same hai — *is call ne
    kuch nahi badla*:
      1. Aisa koi approval row nahi hai.
      2. Row hai lekin **already decided** hai (`decision IS NULL` fail hua).

    🔒 Case 2 hi asli security point hai. Operator UI pe do baar "Approve"
       click kare (ya do tab khuli hon), toh doosra call `None` deta hai — wo
       decision ko overwrite nahi karta, aur `decided_at`/`decided_by` ka audit
       trail pehle wale click ka rehta hai. Ek `UPDATE ... SET decision = %s`
       bina `AND decision IS NULL` ke ek reject ko chup-chaap approve mein badal
       sakta tha.

    ⚠️ `decided_by` abhi hamesha `'operator'` hai — authentication nahi hai
       (`docs/THREAT_MODEL.md` §5). Column aaj exist karta hai taaki jis din
       auth aaye, schema migrate na karni pade.
    """
    if decision not in ("approved", "rejected"):
        # DB ka CHECK bhi yahi karta hai (`init.sql:174`); yahan pehle rok dena
        # ek saaf ValueError deta hai, ek IntegrityError ke bajaye.
        raise ValueError(
            f"decision '{decision}' invalid — sirf 'approved' ya 'rejected'."
        )
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                _SQL_DECIDE_APPROVAL, (decision, decided_by, incident_id, attempt)
            )
            return cur.fetchone()


# ═════════════════════════════════════════════════════════════════════════════
# 7. 🔥 verdicts — oracle ka faisla. Is project ka headline data isi table mein.
# ═════════════════════════════════════════════════════════════════════════════
#
# Do column, do sach:
#   `sustained_recovered` — 30 s tak ~20 rps load ke neeche har probe pass hui.
#   `naive_recovered`     — ek single probe pass hui (control group).
#
# 🔥 N4 metric wahi rows hai jahan ye **do alag** hain. `naive=true` aur
#    `sustained=false` ka matlab: naive check ne kaha "theek ho gaya", asli
#    check ne kaha "nahi". F4 (connection pool exhaustion) pe yahi hota hai,
#    aur wo is project ka poora argument hai.
#
# 🔒 `CLAUDE.md` §3: ye rows **sirf** `oncall/oracle/health.py` se aati hain.
#    LLM kabhi verdict nahi likhta. Isliye is section mein `update_verdict()`
#    jaisa koi function nahi hai — verdict immutable hai. Ek verdict ko badalna
#    "measurement ko badalna" hai, aur wo cheez is codebase mein possible nahi
#    honi chahiye.

_VERDICT_COLUMNS = (
    "id, incident_id, attempt, sustained_recovered, naive_recovered, "
    "probes, load_rps, window_s, at"
)

_SQL_INSERT_VERDICT = (
    "INSERT INTO verdicts (incident_id, attempt, sustained_recovered, "
    "naive_recovered, probes, load_rps, window_s) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id"
)
_SQL_GET_VERDICT = (
    f"SELECT {_VERDICT_COLUMNS} FROM verdicts WHERE incident_id = %s AND attempt = %s"
)
_SQL_LIST_VERDICTS = (
    f"SELECT {_VERDICT_COLUMNS} FROM verdicts WHERE incident_id = %s ORDER BY attempt"
)


def insert_verdict(*, incident_id: int, attempt: int, row: Mapping[str, Any]) -> int:
    """🔥 Oracle ka verdict likho. `row` = `oracle.health.Verdict.to_row()`.

    🔴 Ye function apna naam **health.py se udhaar** leta hai, ulta nahi.
       `oncall/oracle/health.py` mein `Verdict.to_row()` ka docstring likha hai
       *"`db.queries.insert_verdict()` ke liye ready dict"* — matlab wo file is
       naam pe commit kar chuki thi. Isliye yahan naam `insert_verdict` hai,
       `record_verdict` ya `save_verdict` nahi. **Ek naam do jagah likha ho toh
       wo ek contract hai**, style choice nahi.

    `row` mein exactly paanch key aati hain (health.py `to_row()`):
    `sustained_recovered`, `naive_recovered`, `probes`, `load_rps`, `window_s`.
    Hum unko **naam se** nikalte hain, `row.values()` se nahi — dict ka order pe
    depend karna wo bug hai jo `to_row()` mein ek key add hone pe chup-chaap
    columns shift kar deta.

    🔒 `UNIQUE (incident_id, attempt)` (`init.sql:213`): ek attempt ka ek hi
       verdict. Dobara likhne ki koshish `UniqueViolation` hai, jo sahi hai —
       "oracle do baar chala" ek bug hai, retry-worthy situation nahi.
    """
    probes = row["probes"]
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                _SQL_INSERT_VERDICT,
                (
                    incident_id,
                    attempt,
                    bool(row["sustained_recovered"]),
                    bool(row["naive_recovered"]),
                    _jsonb(probes),
                    int(row["load_rps"]),
                    int(row["window_s"]),
                ),
            )
            return int(cur.fetchone()["id"])


def get_verdict(incident_id: int, attempt: int) -> dict[str, Any] | None:
    """Ek attempt ka verdict, ya `None` agar oracle abhi chala hi nahi."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_GET_VERDICT, (incident_id, attempt))
            return cur.fetchone()


def list_verdicts(incident_id: int) -> list[dict[str, Any]]:
    """Ek incident ke saare verdicts, attempt order mein.

    Ek incident pe 3 attempt tak ho sakte hain, matlab 3 verdicts. UI timeline
    aur `docs/METRICS.md` ka N1 (attempts-to-fix) dono isko padhte hain.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_LIST_VERDICTS, (incident_id,))
            return list(cur.fetchall())


# ═════════════════════════════════════════════════════════════════════════════
# 8. 🔥 jobs — Postgres hi humari queue hai (D10). Celery nahi, Redis nahi.
# ═════════════════════════════════════════════════════════════════════════════
#
# Poora tark `docs/TEACH_PHASE4.md` §5 mein hai. Ek paragraph ka version:
#
# `SELECT ... FOR UPDATE SKIP LOCKED` = "mujhe wo queued row do jise koi doosra
# worker abhi pakad ke nahi baitha hai". `FOR UPDATE` row-lock leta hai (jo is
# transaction ke end tak chalta hai), aur `SKIP LOCKED` doosre worker ko us row
# pe **intezaar karne se rokta hai** — wo agli row utha leta hai.
#
# Reject kiye gaye do tarike:
#   • plain `UPDATE ... WHERE id = (SELECT ... LIMIT 1)` — do worker phir bhi
#     same id chun sakte hain; ek ka UPDATE 0 rows karega aur retry loop chahiye.
#   • plain `SELECT ... FOR UPDATE` (bina SKIP LOCKED) — doosra worker us row pe
#     **block** ho jaata hai. Ek slow job poori fleet ko rok deti hai.
#
# 🔴 `ORDER BY id` cosmetic nahi hai: uske bina Postgres kisi bhi order mein rows
#    de sakta hai, aur bad-luck se ek job hamesha ke liye bhookhi (starved) reh
#    sakti hai. `ORDER BY id` = FIFO.
#
# 🔴 Ye poora mechanism `stack/oncalldb/init.sql:355` ke **partial index** pe
#    khada hai: `CREATE INDEX jobs_queue_idx ON jobs (id) WHERE status='queued'`.
#    `done` rows index mein hain hi nahi, isliye 10,000 finished jobs ke baad bhi
#    claim same speed pe rehta hai.

# 🔴 `enqueue_job()` neeche `_SQL_INSERT_JOB` reuse karta hai, jo §1 mein
#    (`open_incident()` ke saath) define hua hai. Do jagah same INSERT likhna
#    wo duplication hai jo ek din chup-chaap divergent ho jaata hai.

# 🔥 Is project ka sabse important SQL. Do statement, ek transaction:
#    (1) ek queued row lock karo, (2) usi row ko 'running' mark karo.
_SQL_CLAIM_SELECT = (
    "SELECT id, incident_id, attempts FROM jobs "
    "WHERE status = 'queued' "
    "ORDER BY id "
    "FOR UPDATE SKIP LOCKED "
    "LIMIT 1"
)
_SQL_CLAIM_MARK = (
    "UPDATE jobs SET status = 'running', locked_at = now(), locked_by = %s, "
    "updated_at = now() WHERE id = %s"
)
_SQL_FINISH_JOB = (
    "UPDATE jobs SET status = 'done', locked_at = NULL, locked_by = NULL, "
    "updated_at = now() WHERE id = %s AND status = 'running'"
)
_SQL_FAIL_JOB_RETRY = (
    "UPDATE jobs SET status = 'queued', locked_at = NULL, locked_by = NULL, "
    "attempts = attempts + 1, last_error = %s, updated_at = now() "
    "WHERE id = %s AND status = 'running'"
)
_SQL_FAIL_JOB_DEAD = (
    "UPDATE jobs SET status = 'failed', locked_at = NULL, locked_by = NULL, "
    "attempts = attempts + 1, last_error = %s, updated_at = now() "
    "WHERE id = %s AND status = 'running'"
)
# ⚠️ Interval ko SQL mein interpolate **nahi** karte — `make_interval` ko
#    seconds ek bound parameter ki tarah deta hai. `now() - '%s seconds'` likhna
#    ek string ke andar placeholder daalna hai, jo psycopg dekhta bhi nahi.
_SQL_REAP_STALE = (
    "UPDATE jobs SET status = 'queued', locked_at = NULL, locked_by = NULL, "
    "last_error = 'reaped: lock stale', updated_at = now() "
    "WHERE status = 'running' AND locked_at < now() - make_interval(secs => %s) "
    "RETURNING id, incident_id, locked_by"
)
_SQL_GET_JOB = (
    "SELECT id, incident_id, status, locked_at, locked_by, attempts, "
    "last_error, created_at, updated_at FROM jobs WHERE id = %s"
)
_SQL_QUEUE_DEPTH = (
    "SELECT status, count(*) AS n FROM jobs GROUP BY status ORDER BY status"
)


def enqueue_job(incident_id: int) -> int:
    """Ek incident ke liye naya job. `open_incident()` pehla job khud daalta hai.

    🔒 `jobs_one_active_per_incident_idx` (`init.sql`, partial UNIQUE index) ek
       incident pe ek hi active (`queued`/`running`) job allow karta hai. Wo
       constraint hi wo cheez hai jo "ek incident, ek worker" guarantee karti
       hai — kisi Python check se nahi, DB se. Dobara enqueue karne ki koshish
       `UniqueViolation` hai, aur wo **sahi** behaviour hai.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_INSERT_JOB, (incident_id,))
            return int(cur.fetchone()["id"])


def claim_job(worker_id: str) -> dict[str, Any] | None:
    """🔥 Ek queued job utha ke `running` mark karo. `None` = queue khaali.

    🔴 Ye do statement **ek hi transaction** mein hone zaruri hain. `FOR UPDATE`
       ka lock transaction ke end tak jeeta hai; agar `SELECT` aur `UPDATE` alag
       transaction hote, toh lock `SELECT` ke turant baad chhoot jaata aur
       doosra worker usi row ko claim kar leta — matlab wahi double-execution
       bug jisse bachne ke liye ye poora mechanism hai.

    Isliye ye function apna `get_conn()` khud kholta hai aur caller se
    connection nahi leta: transaction boundary is function ke andar rehni
    chahiye, warna "exactly-once claim" ka vaada ye function nibha nahi sakta.

    Args:
        worker_id: kisne uthaya — `locked_by` mein jaata hai. Reaper ke logs
            mein yahi batata hai ki kaunsa worker mara tha.

    Returns:
        `{"id", "incident_id", "attempts"}` ya `None`.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_CLAIM_SELECT)
            row = cur.fetchone()
            if row is None:
                return None  # queue khaali — normal, error nahi.
            cur.execute(_SQL_CLAIM_MARK, (worker_id, row["id"]))
            return {
                "id": int(row["id"]),
                "incident_id": int(row["incident_id"]),
                "attempts": int(row["attempts"]),
            }


def finish_job(job_id: int) -> bool:
    """Job successfully khatam. `False` = wo job `running` nahi thi.

    `AND status = 'running'` ka matlab: ek reaped job (jo wapas `queued` ho gayi)
    ko purana worker `done` mark nahi kar sakta. Wo worker der se jaaga hai; uska
    kaam abandoned hai. `False` return karo, exception nahi — caller (worker) ko
    isko log karke aage badhna hai, kyunki iska matlab hai "tumhari job kisi aur
    ne le li".
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_FINISH_JOB, (job_id,))
            return cur.rowcount == 1


def fail_job(job_id: int, error: str, *, max_attempts: int = MAX_ATTEMPTS) -> str:
    """Job fail hui. Retry karni hai ya dafan? Returns `'queued'` ya `'failed'`.

    🔴 Faisla `attempts` pe hota hai, lekin hum wo value **DB se padhte hain**,
       caller se nahi lete. Caller ki value stale ho sakti hai (reaper ne beech
       mein badal di ho), aur "kitni baar try hua" ka ek hi sach hona chahiye.

    Do jagah attempt count hai is project mein aur wo **alag cheezein** hain:
      • `jobs.attempts` — job kitni baar chalayi gayi (infra-level retry).
      • `incidents.attempt` — agent ne kitne fix propose kiye (logic-level).
    Ek job crash ho ke retry ho sakti hai bina naya fix propose karne ke, isliye
    do counter hain. `docs/GLOSSARY.md` mein dono ki definition alag hai.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_GET_JOB, (job_id,))
            job = cur.fetchone()
            if job is None or job["status"] != "running":
                return "ignored"
            # attempts + 1 = is fail ke baad ki count.
            if int(job["attempts"]) + 1 >= max_attempts:
                cur.execute(_SQL_FAIL_JOB_DEAD, (error[:2000], job_id))
                return "failed"
            cur.execute(_SQL_FAIL_JOB_RETRY, (error[:2000], job_id))
            return "queued"


def reap_stale_jobs(*, stale_after_s: int = JOB_STALE_AFTER_S) -> list[dict[str, Any]]:
    """Mare hue worker ki jobs wapas queue mein daalo. Returns reaped rows.

    **Problem ye solve karta hai:** worker ne job claim ki (`running`), phir
    process mar gaya (OOM, `docker kill`, laptop band). Us job pe koi lock nahi
    bacha — Postgres ka lock connection ke saath chala gaya — lekin `status`
    hamesha ke liye `running` reh gaya. Koi use claim nahi karega, aur incident
    latak jaayega **bina kisi error ke**. Wahi "sabse bura kism ka bug".

    Isliye lock ki asli clock `locked_at` column hai, Postgres ka lock nahi.
    Postgres ka lock crash-safe hai (connection gaya, lock gaya) — lekin wo
    hamein *batata* nahi ki job anaath ho gayi. `locked_at` batata hai.

    ⚠️ `stale_after_s` default 600 s ek **estimate** hai, measurement nahi
       (2026-08-22). Poora tark `JOB_STALE_AFTER_S` ke paas likha hai. Bahut
       chhota value (jaise 60 s) ek zinda job ko requeue kar deta jab oracle ka
       30 s window chal raha ho — do action, ek incident.

    Returns:
        Reaped rows ki list. **Khaali list normal hai** — reaper har 30 s
        chalta hai aur zyada tar kuch nahi milta. Caller ko non-empty list
        **log** karni chahiye: wo ek crash ka saboot hai.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_REAP_STALE, (stale_after_s,))
            return list(cur.fetchall())


def get_job(job_id: int) -> dict[str, Any] | None:
    """Ek job ka poora row. Debugging aur `/api/stats` ke liye."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_GET_JOB, (job_id,))
            return cur.fetchone()


def queue_depth() -> dict[str, int]:
    """`{'queued': 3, 'running': 1, ...}` — health endpoint ka ek number.

    🔴 Ye `GROUP BY status` hai, chaar alag `count(*)` query nahi. Chaar query ka
       matlab chaar snapshot different time pe — total kisi bhi ek moment se
       match nahi karta. Ek query = ek consistent snapshot.

    Missing status keys ko 0 se bhara jaata hai, taaki caller ko
    `.get('failed', 0)` likhna na pade aur UI mein `undefined` na dikhe.
    """
    out = {"queued": 0, "running": 0, "done": 0, "failed": 0}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_QUEUE_DEPTH)
            for row in cur.fetchall():
                out[str(row["status"])] = int(row["n"])
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 9. 🔒 runbooks — sirf READ. Writes yahan **jaanbujh ke nahi** hain.
# ═════════════════════════════════════════════════════════════════════════════
#
# 🔴 Ye section ka sabse important hissa wo hai jo yahan **nahi** likha hai.
#
# `runbooks` table mein do column hain jo is file mein aa hi nahi sakte:
#   • N4 ka **answer-key column** — wo ground truth hai. `CLAUDE.md` §3:
#     *"Ground truth is invisible to the agent."* `oncall/` ke andar us column
#     ka naam likha hua bhi nahi mil sakta (`make lint` check 4 isko grep karta
#     hai), isliye yahan sirf describe kiya gaya hai. Authoritative naam
#     `CLAUDE.md` §3 aur `Makefile` ke `lint` target mein hai.
#   • `embedding vector(384)` — ~1.5 KB per row. 50-row search 75 KB kheenchti,
#     jiski kisi ko zarurat nahi. (Vector *comparison* Postgres ke andar hota
#     hai — `<=>` operator — isliye embedding kabhi Python tak aati hi nahi.)
#
# Isliye **ingest ka SQL is file mein nahi hai**. `scripts/ingest_runbooks.py`
# apna INSERT khud likhta hai, kyunki usko answer-key column aur `embedding`
# dono likhne padte hain — aur wo `scripts/` mein hai, `oncall/` mein nahi.
#
# ⚠️ Ye "SQL sirf queries.py mein" rule ka ek **jaanbujh ke banaya exception**
#    hai, aur wo rule ke khilaaf nahi jaata — rule ka maksad tha "leak audit ek
#    grep ho". Yahan wo maksad *behtar* poora hota hai: leak-capable SQL ek aisi
#    file mein hai jo `oncall/` ke bahar hai, matlab `oncall/` par grep karne se
#    hi guarantee mil jaati hai. Agar ingest yahan hota, toh `make lint` ka
#    check 4 is file ko permanently exempt karna padta — aur ek exempted file
#    baad mein asli leak ki jagah ban jaati.

_SQL_GET_RUNBOOK = f"SELECT {', '.join(RUNBOOK_COLUMNS)} FROM runbooks WHERE id = %s"
_SQL_LIST_RUNBOOKS = (
    f"SELECT {', '.join(RUNBOOK_COLUMNS)} FROM runbooks ORDER BY id LIMIT %s"
)
_SQL_COUNT_RUNBOOKS = (
    "SELECT count(*) AS n, count(embedding) AS embedded FROM runbooks"
)
_SQL_RUNBOOK_SOURCES = (
    "SELECT source, licence, count(*) AS n, max(fetched_at) AS latest "
    "FROM runbooks GROUP BY source, licence ORDER BY source"
)


def get_runbook(runbook_id: str) -> dict[str, Any] | None:
    """Ek runbook, `RUNBOOK_COLUMNS` ke hisaab se — answer-key column kabhi nahi."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_GET_RUNBOOK, (runbook_id,))
            return cur.fetchone()


def list_runbooks(*, limit: int = 100) -> list[dict[str, Any]]:
    """Corpus browse karne ke liye. UI ka read-only view."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_LIST_RUNBOOKS, (limit,))
            return list(cur.fetchall())


def runbook_stats() -> dict[str, int]:
    """`{'total': 50, 'embedded': 50}` — ingest ke baad ka sanity check.

    🔴 `count(embedding)` aur `count(*)` ka difference load-bearing hai: SQL mein
       `count(column)` NULL rows ko **chhod** deta hai. Toh `embedded < total` ka
       matlab hai "kuch runbooks bina embedding ke padi hain" — aur wo silently
       retrieval ko todta hai (RAG on hai lekin us runbook ko dhoondh hi nahi
       sakta). `make oracle`-style pre-flight check isi ek line pe khada hai.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_COUNT_RUNBOOKS)
            row = cur.fetchone() or {}
            return {
                "total": int(row.get("n") or 0),
                "embedded": int(row.get("embedded") or 0),
            }


def runbook_sources() -> list[dict[str, Any]]:
    """Provenance report: kaunsa source, kaunsa licence, kitne rows, kab aaye.

    `CLAUDE.md` §3: *"Every ingested record stores its source, licence, and fetch
    date."* Ye function wo claim **check karne** ka tarika hai — `docs/DATA.md`
    ki licence table isi query ke output se bharni hai, yaad se nahi.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_RUNBOOK_SOURCES)
            return list(cur.fetchall())


# ═════════════════════════════════════════════════════════════════════════════
# 10. retrievals — 🔥 N4 ka raw data. Retrieval ka EKMATRA writer yahin.
# ═════════════════════════════════════════════════════════════════════════════
#
# 🔴 Ye rows LLM nahi bharta — `retrieval/search.py` ka plain code bharta hai.
#    `rank`, `rrf_score`, `vector_rank`, `keyword_rank` — sab arithmetic hai,
#    kisi model ki opinion nahi. Isliye N4 gate G1 paas karta hai.

_RETRIEVAL_COLUMNS = (
    "id, incident_id, attempt, runbook_id, rank, rrf_score, vector_rank, keyword_rank"
)

_SQL_INSERT_RETRIEVAL = (
    "INSERT INTO retrievals (incident_id, attempt, runbook_id, rank, rrf_score, "
    "vector_rank, keyword_rank) VALUES (%s, %s, %s, %s, %s, %s, %s)"
)
_SQL_GET_RETRIEVALS = (
    f"SELECT {_RETRIEVAL_COLUMNS} FROM retrievals "
    "WHERE incident_id = %s AND attempt = %s ORDER BY rank"
)
_SQL_LIST_RETRIEVALS = (
    f"SELECT {_RETRIEVAL_COLUMNS} FROM retrievals WHERE incident_id = %s ORDER BY attempt, rank"
)


def insert_retrievals(
    incident_id: int, attempt: int, rows: Sequence[Mapping[str, Any]]
) -> int:
    """Fusion ka output likho — top-k, ek transaction mein.

    🔴 Ek transaction isliye ki "3 mein se 2 likhe gaye" wali adhoori state na
       bane. N4 ki query partial data pe galat hit-rate deti — aur wo galti
       dikhti nahi, number bas thoda alag aata.

    Args:
        rows: har row mein `runbook_id`, `rank` (1-based), `rrf_score`,
              optional `vector_rank` / `keyword_rank`.

    Returns:
        Kitni rows likhi gayin.
    """
    if not rows:
        # Khaali fusion result valid hai (corpus khaali / sab filtered). INSERT
        # loop chalana bekaar hai; caller ko ginti hi kaafi hai.
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    _SQL_INSERT_RETRIEVAL,
                    (
                        incident_id,
                        attempt,
                        str(r["runbook_id"]),
                        int(r["rank"]),
                        float(r["rrf_score"]),
                        r.get("vector_rank"),
                        r.get("keyword_rank"),
                    ),
                )
    return len(rows)


def get_retrievals(incident_id: int, attempt: int) -> list[dict[str, Any]]:
    """Ek attempt ki ranked list. UI ka citation card + N4 dono isi se."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_GET_RETRIEVALS, (incident_id, attempt))
            return list(cur.fetchall())


def list_retrievals(incident_id: int) -> list[dict[str, Any]]:
    """Saare attempts ki retrieval history."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_LIST_RETRIEVALS, (incident_id,))
            return list(cur.fetchall())


# ═════════════════════════════════════════════════════════════════════════════
# 11. recheck — R10 ka delayed verification. Oracle ko bhi measure kiya jaata hai.
# ═════════════════════════════════════════════════════════════════════════════
#
# 30-second window chala gaya aur RECOVERED mila — lekin agar fix 5 minute baad
# bhi tika rahta hai tabhi oracle sach bol raha hai. Ye query wo incidents
# dhoondhti hai jo RECOVERED hue, 5–60 minute purane hain, aur jinka recheck
# abhi tak NAHI hua (`NOT EXISTS`). Worker ka background sweep isi ko chalata
# hai; natija `events` mein kind='verify' payload `{recheck_5min_ok}` ke saath
# jaata hai — naya table nahi, naya column nahi.

_SQL_RECHECK_DUE = (
    "SELECT i.id AS incident_id, i.service "
    "FROM incidents i "
    "WHERE i.state = 'RECOVERED' "
    "AND i.closed_at < now() - make_interval(secs => %s) "
    "AND i.closed_at > now() - make_interval(secs => %s * 12) "
    "AND NOT EXISTS ("
    "    SELECT 1 FROM events e "
    "    WHERE e.incident_id = i.id AND e.kind = 'verify' "
    "    AND e.payload ? 'recheck_5min_ok'"
    ") "
    "LIMIT %s"
)


def list_recheck_due(
    *, after_s: int = 300, limit: int = 10
) -> list[dict[str, Any]]:
    """RECOVERED incidents jinhe 5 minute ke baad dobara probe karna hai.

    `after_s=300` (5 min) aur upper bound `after_s*12` (1 ghanta) — window
    isliye taaki band-padi DB mein purane incidents baar-baar recheck na hon
    (ek ghante baad recheck ka diagnostic value zero hai).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_RECHECK_DUE, (after_s, after_s, limit))
            return list(cur.fetchall())
