"""Allowlist — 9 actions. Is repo ka security boundary yahi ek file hai.

────────────────────────────────────────────────────────────────────────────
Allowlist kya hai, aur denylist kyun nahi (CLAUDE.md §1 — pehle samjho)
────────────────────────────────────────────────────────────────────────────

Do tareeke hote hain ye decide karne ke:

    Denylist  = "ye cheezein mat karo, baaki sab allowed"
    Allowlist = "sirf ye cheezein karo, baaki sab blocked"

Hum allowlist use karte hain, aur wajah ek hi hai: **LLM ka output attacker-
controlled maana jaata hai.** Log shopapp ke container se aate hain. Agar
koi request `"; DROP TABLE orders; --"` jaisa string log karwa de, wo string
LLM ke prompt mein pahunch jaata hai (prompt injection). LLM phir kuch bhi
propose kar sakta hai.

Denylist mein aap wo sochte ho jo aapko *yaad aaya*. Allowlist mein aap wo
likhte ho jo aapne *soch-samajh kar allow kiya*. Injection ke against pehla
wala haar jaata hai — kyunki attacker ke paas wo idea ho sakta hai jo aapko
nahi aaya. Isliye: 9 naam, poori list, aur inke bahar kuch bhi = reject.

🔒 CLAUDE.md §3: "Adding an action means editing four places — allowlist.py,
handlers.py, llm/schema.py (the Literal), and tests/test_broker.py. Three out
of four is a hole, not a partial job."

Ye file un chaar mein **pehli** hai, aur baaki teen isi se derive hoti hain:

    allowlist.py   → naam + params + kya allowed hai   (ye file, authority)
    handlers.py    → har naam ke liye asli Docker call
    llm/schema.py  → Literal[...] taaki LLM galat naam bhej hi na sake
    test_broker.py → adversarial tests: kaunsi layer ne reject kiya

────────────────────────────────────────────────────────────────────────────
9 kyun, aur ye 9 kyun (D41)
────────────────────────────────────────────────────────────────────────────

Menu mein sirf sahi jawab rakhna = exam mein sirf ek option dena. Wo kuch
measure nahi karta. Toh 9 = 5 + 3 + 1:

    5  correct actions  — taxonomy ke paanch faults ka sahi jawab
    3  distractors      — plausible galat jawab, jaan-boojh kar rakhe gaye
    1  escalate         — legal "mujhe nahi pata"

🔴 Distractors asli hain, dummy nahi. Handler unhe **actually chalata hai**,
aur phir oracle unhe fail karta hai. Fake action rakhna cheating hoti —
loop ko sach batana chahiye ki agent ne galat kaam kiya aur wo kaam hua bhi.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

# ─────────────────────────────────────────────────────────────────────────────
# 🔴 Jo actions docs mein likhe the aur yahan se HATA diye gaye — aur kyun
# ─────────────────────────────────────────────────────────────────────────────
#
# Docs (TDD §3c, LEARNINGS §) mein ek purani 9-item list thi jo galat thi.
# Wo list ye kehti thi:
#
#     restart_service, start_service, set_config_key, set_db_setting,
#     set_container_memory, clear_directory, scale_pool, rollback_config,
#     escalate
#
# 🔴 Us list mein F3 ka sahi jawab — `kill_blocking_queries` — **tha hi nahi**.
#    Matlab wo list us duniya ko describe karti thi jahan F3 fixable hi nahi.
#    Ye ek doc-only fantasy thi; `chaos/faults.py` (asli code) apne ground-truth
#    field mein F3 ke liye `kill_blocking_queries` likhta hai. Code jeeta.
#
#    ⚠️ Us field ka **asli naam yahan likha nahi ja sakta** — `make lint` ka
#       check #4 poore `oncall/` tree mein us token ko dhoondhta hai aur usme
#       koi exception mechanism nahi hai (na `# noqa`, na `# lint-ok`). Wahi
#       usool `oncall/__init__.py` ke docstring mein bhi likha hai: describe
#       karo, spell mat karo. Authoritative list do jagah hai — `Makefile` ka
#       `lint` target, aur `CLAUDE.md` §3.
#
# Chaar actions hata diye, har ek ki wajah likhi hai:
#
#   set_config_key       — `set_db_setting` ka duplicate tha, bas dhundhla naam.
#                          Do actions jo ek hi kaam karte hain = F4 ka scoring
#                          ambiguous ho jaata (do "sahi" jawab). Duplicate
#                          hataana hi imaandaar fix hai.
#   set_container_memory — jis fault class ko ye theek karta (OOM) wo taxonomy
#                          mein hai hi nahi. Aur macOS pe Docker daemon ek Linux
#                          VM ke andar chalta hai, aur wahan chalu container pe
#                          `container.update(mem_limit=...)` reliably supported
#                          nahi hai — jo action chal na sake, wo allowlist mein
#                          jhoot hai.
#                          ⚠️ Kis VM tool se (`make lint` check #5, D22) — wo
#                          naam runtime code mein nahi aata: code kisi bhi
#                          Docker-API-compatible daemon pe chalna chahiye, aur
#                          ek naam likhne se wo coupling code mein ghus jaati hai.
#   rollback_config      — "pichhli known-good config" ke liye config snapshot
#                          store chahiye. Wo subsystem exist nahi karta.
#   scale_pool           — shopapp ka pool size env (`POOL_MAX`) se aata hai,
#                          aur Docker chalu container ka env badal nahi sakta.
#                          Iske liye container ko remove + recreate karna padta
#                          (poora run-spec dobara banao) — ek distractor ke
#                          liye bahut bhaari, aur fragile. `vacuum_table` isi
#                          jagah behtar distractor hai: sasta, asli, aur F3 ke
#                          liye zyada tempting.


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2 ka raw material: kya-kya value legal hai
# ─────────────────────────────────────────────────────────────────────────────

#: 🔥 `oncall-db` yahan JAAN-BOOJH KAR nahi hai.
#:
#: shopapp/shopdb/shopcache = victim stack, inhe todna aur theek karna kaam hai.
#: `oncall-db` = agent ka **apna** brain (incidents, actions, approvals).
#: Agar agent apne hi database ko restart/stop kar sakta, toh ek galat proposal
#: usi trace ko uda deta jo galti ka saboot hai. Ek system jo apni memory mita
#: sakta hai, wo audit nahi ho sakta.
ALLOWED_SERVICES: frozenset[str] = frozenset({"shopapp", "shopdb", "shopcache"})

#: `clear_directory` sirf in do paths pe chal sakta hai.
#:
#: 🔴 `/var/lib/postgresql/data` yahan nahi hai (THREAT_MODEL T1). Wo Postgres
#: ka data directory hai — usse clear karna "fix" nahi, data loss hai. Ek
#: hallucinating LLM `clear_directory("/var/lib/postgresql/data")` propose kar
#: sakta hai aur ye set use blank kar deta hai. Ye ek line hi wo blast radius
#: hai jo is action ka.
ALLOWED_PATHS: frozenset[str] = frozenset({"/tmp/shopapp-cache", "/var/log/shopapp"})

#: `set_db_setting` ki keys, aur har key ki legal range.
#:
#: 🔴 `fsync` yahan nahi hai. `ALTER SYSTEM SET fsync = off` Postgres ko tez
#: kar deta hai aur crash pe data corrupt kar deta hai — classic "benchmark
#: trick jo production kill karti hai". Boolean keys allowlist se bahar hain.
#:
#: 🔴 `max_connections` ka **floor 10 kyun** (0 ya 1 nahi):
#:    PG15 mein `superuser_reserved_connections` default 3 hai, aur rule hai
#:    "must be less than max_connections". Toh `max_connections = 2` invalid
#:    hai — Postgres boot hi nahi karega. Ek action jo service ko boot-loop
#:    mein daal de, wo fix nahi, naya fault hai. Floor 10 us cliff se safe
#:    doori pe hai.
#:
#: 🔴 Aur floor 10 **phir bhi** galat-value wali galti allow karta hai (D40):
#:    `POOL_MAX=10` hai, aur 10 client connections ke liye chahiye
#:    10 + 3 (superuser reserve) = 13. Toh `value=10` ya `11` ya `12` bhi
#:    load pe fail hoga. Sahi action + naakaafi value ka case zinda hai —
#:    aur us case ka faisla **oracle** karta hai, ground truth nahi.
ALLOWED_DB_KEYS: Mapping[str, tuple[int, int]] = {
    # key                 (min, max)
    "max_connections": (10, 500),
    "statement_timeout": (0, 600_000),  # milliseconds
    "work_mem": (64, 262_144),  # kilobytes
}

#: `vacuum_table` / future table-scoped actions sirf in tables pe.
#: shopdb mein aaj sirf ek table hai (`stack/shopdb/init.sql:26`).
ALLOWED_TABLES: frozenset[str] = frozenset({"orders"})


# ─────────────────────────────────────────────────────────────────────────────
# ActionSpec — ek action ka poora contract, data ke roop mein
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ActionSpec:
    """Ek allowed action ka contract.

    Ye jaan-boojh kar **data** hai, code nahi (koi `validate()` method nahi).
    Wajah: `handlers.py`, `llm/schema.py` aur `tests/test_broker.py` — teeno
    isi dict se apni list nikaalte hain. Agar spec code hota (har action ki
    apni class + method), toh un teen jagahon ko sync karna manual ho jaata,
    aur CLAUDE.md §3 ka "chaar jagah" rule silently toot jaata. Data ko loop
    kiya ja sakta hai; code ko nahi.

    Fields:
        name             — wahi string jo LLM JSON mein bhejta hai
        params           — required param naam, exact order mein
        services         — kaunse services pe ye action legal hai
        idempotent       — do baar chalane se result badalta hai ya nahi
        needs_restart    — asar dikhne ke liye container restart chahiye
        purpose          — 'correct' | 'distractor' | 'terminal'
        fixes            — kaunse fault ids ka ye sahi jawab hai (docs ke liye)
        what_it_does     — approval screen pe insaan ko dikhaya jaata hai
    """

    name: str
    params: tuple[str, ...]
    services: frozenset[str]
    idempotent: bool
    needs_restart: bool
    purpose: str
    fixes: tuple[str, ...]
    what_it_does: str


# ─────────────────────────────────────────────────────────────────────────────
# 🔒 The allowlist. Nau. Isse zyada nahi.
# ─────────────────────────────────────────────────────────────────────────────

_DB_ONLY = frozenset({"shopdb"})
_CACHE_ONLY = frozenset({"shopcache"})

ACTIONS: Mapping[str, ActionSpec] = {
    # ── 1/9 ─ CORRECT for F1 ────────────────────────────────────────────────
    "restart_service": ActionSpec(
        name="restart_service",
        params=("service",),
        services=ALLOWED_SERVICES,
        idempotent=True,
        needs_restart=False,  # ye khud restart hai
        purpose="correct",
        fixes=("F1",),
        what_it_does="Stops the container and starts it again (SIGTERM, then SIGKILL).",
    ),
    # ── 2/9 ─ CORRECT for F5 ────────────────────────────────────────────────
    # 🔴 `start_service` aur `restart_service` alag kyun hain:
    # Docker ka `restart` ek *chalu* container pe chalta hai. F5 mein shopcache
    # `exited` hai — `restart` uspe bhi kaam kar jaata hai, lekin verb galat
    # hai. `start` = "band cheez ko chalu karo", `restart` = "chalu cheez ko
    # naya karo". Do naam rakhne se hum measure kar sakte hain ki LLM ne
    # container ki **state padhi** ya bas reflex mein restart likh diya.
    # (Isliye `chaos/faults.py` mein F5 `restart_service` ko bhi ek acceptable
    #  answer maanta hai — us field ka naam yahan likha nahi ja sakta,
    #  `Makefile` ka `lint` check 4 dekho.)
    "start_service": ActionSpec(
        name="start_service",
        params=("service",),
        services=ALLOWED_SERVICES,
        idempotent=True,
        needs_restart=False,
        purpose="correct",
        fixes=("F5",),
        what_it_does="Starts a stopped (exited) container.",
    ),
    # ── 3/9 ─ CORRECT for F2 ────────────────────────────────────────────────
    "clear_directory": ActionSpec(
        name="clear_directory",
        params=("service", "path"),
        services=ALLOWED_SERVICES,
        idempotent=True,  # khaali directory ko dobara khaali karna = no-op
        needs_restart=False,
        purpose="correct",
        fixes=("F2",),
        what_it_does="Deletes everything inside the given directory (the directory itself stays).",
    ),
    # ── 4/9 ─ CORRECT for F3 ────────────────────────────────────────────────
    # 🔴 Ye action docs ki purani list mein tha hi nahi — aur F3 ka sahi jawab
    #    yahi hai. Isi hole ki wajah se ye file pehle likhi gayi, docs baad mein.
    "kill_blocking_queries": ActionSpec(
        name="kill_blocking_queries",
        params=("service",),
        services=_DB_ONLY,
        idempotent=True,  # koi blocker na ho toh 0 rows terminate hote hain
        needs_restart=False,
        purpose="correct",
        fixes=("F3",),
        what_it_does=(
            "Terminates the sessions blocking others (pg_stat_activity → "
            "pg_terminate_backend()). Only blockers — its own connection and "
            "idle sessions are left alone."
        ),
    ),
    # ── 5/9 ─ CORRECT for F4 ────────────────────────────────────────────────
    "set_db_setting": ActionSpec(
        name="set_db_setting",
        params=("service", "key", "value"),
        services=_DB_ONLY,
        idempotent=True,  # wahi value dobara set karo, wahi rahegi
        needs_restart=True,  # 🔴 max_connections reload se nahi badalta
        purpose="correct",
        fixes=("F4",),
        what_it_does=(
            "Runs ALTER SYSTEM SET <key> = <value> (written to postgresql.auto.conf), "
            "then restarts the container."
        ),
    ),
    # ── 6/9 ─ DISTRACTOR ────────────────────────────────────────────────────
    # Kabhi sahi jawab nahi. Do kaam karta hai:
    #   1. Menu ko non-trivial banata hai (LLM ke paas galat karne ki rassi ho).
    #   2. Test karta hai ki ek destructive-dikhne wala verb bhi bounded hai —
    #      `oncall-db` `ALLOWED_SERVICES` mein nahi hai, toh agent apna brain
    #      band nahi kar sakta, chahe wo propose kar de.
    "stop_service": ActionSpec(
        name="stop_service",
        params=("service",),
        services=ALLOWED_SERVICES,
        idempotent=True,
        needs_restart=False,
        purpose="distractor",
        fixes=(),
        what_it_does="Stops the container (SIGTERM, SIGKILL after 5 s). This fixes no fault.",
    ),
    # ── 7/9 ─ DISTRACTOR ────────────────────────────────────────────────────
    # F5 ke liye tempting: "cache down hai → cache saaf karo". Lekin F5 mein
    # shopcache *chal hi nahi raha* — FLUSHALL connect hi nahi kar payega.
    # F2 ke liye bhi tempting: "disk full hai → cache flush karo". Lekin F2
    # mein bhari cheez Redis ki memory nahi, shopapp ki tmpfs directory hai.
    "flush_cache": ActionSpec(
        name="flush_cache",
        params=("service",),
        services=_CACHE_ONLY,
        idempotent=True,
        needs_restart=False,
        purpose="distractor",
        fixes=(),
        what_it_does="Runs FLUSHALL on Redis — all cached data is wiped.",
    ),
    # ── 8/9 ─ DISTRACTOR ────────────────────────────────────────────────────
    # 🔥 F3 ka sabse tempting galat jawab. "orders table pe problem hai →
    # table ko vacuum/analyze karo" — ek asli SRE bhi ye soch sakta hai.
    # Aur ye demonstrably fail hota hai: VACUUM ko khud lock chahiye, aur wo
    # lock F3 ke ACCESS EXCLUSIVE ke peeche queue mein lag jaata hai.
    # Isliye handler `lock_timeout` set karta hai — warna broker 600 s hang.
    "vacuum_table": ActionSpec(
        name="vacuum_table",
        params=("service", "table"),
        services=_DB_ONLY,
        idempotent=True,
        needs_restart=False,
        purpose="distractor",
        fixes=(),
        what_it_does="Runs VACUUM (ANALYZE) <table>. Needs a lock, so it blocks itself when the lock is taken.",
    ),
    # ── 9/9 ─ TERMINAL ──────────────────────────────────────────────────────
    # 🔴 Ye action allowlist mein hona **zaroori** hai, aur wajah design hai:
    # agar "mujhe nahi pata" bolne ka koi legal tareeka na ho, toh LLM ko
    # majboor kar diya jaata hai kuch bhi ek action chunne ke liye. Phir jo
    # aata hai wo diagnosis nahi, guess hota hai. `escalate` uncertainty ko
    # ek first-class output banata hai — aur METRICS.md us "escalated" rate ko
    # alag se ginta hai, failure ke saath mila kar nahi.
    "escalate": ActionSpec(
        name="escalate",
        params=("reason",),
        services=frozenset(),  # koi service touch nahi karta
        idempotent=True,
        needs_restart=False,
        purpose="terminal",
        fixes=(),
        what_it_does="Runs nothing. Hands the incident to a human with the full trace.",
    ),
}

#: LLM schema ka `Literal[...]` isi se banta hai — dono jagah type karna
#: hi wo drift paida karta tha jo is file ne theek kiya.
ACTION_NAMES: tuple[str, ...] = tuple(ACTIONS)


# ─────────────────────────────────────────────────────────────────────────────
# 🔒 Invariants — import time pe check hote hain
# ─────────────────────────────────────────────────────────────────────────────
#
# 🔴 `assert` jaan-boojh kar nahi use kiya. `python -O` saare assert statements
#    bytecode se hata deta hai. Ek security invariant jo optimisation flag se
#    gayab ho jaaye, wo invariant nahi — wo ek comment hai. Isliye explicit
#    `raise RuntimeError`.

if len(ACTIONS) != 9:  # pragma: no cover - import-time invariant
    raise RuntimeError(f"Allowlist mein {len(ACTIONS)} actions hain, 9 hone chahiye")

for _name, _spec in ACTIONS.items():
    if _name != _spec.name:  # pragma: no cover - import-time invariant
        raise RuntimeError(f"dict key {_name!r} spec.name {_spec.name!r} se match nahi karti")
    if not _spec.services <= ALLOWED_SERVICES:  # pragma: no cover
        raise RuntimeError(f"{_name}: services ALLOWED_SERVICES ke bahar hai")
    if _spec.purpose not in {"correct", "distractor", "terminal"}:  # pragma: no cover
        raise RuntimeError(f"{_name}: purpose {_spec.purpose!r} unknown hai")

# 🔴 Ek invariant jo yahan JAAN-BOOJH KAR nahi likha ja sakta:
#
#     "taxonomy ke har fault ka sahi jawab is allowlist mein hona chahiye"
#
# Ye check karne ke liye `chaos.faults` import karna padta — aur CLAUDE.md §3
# kehta hai: kuch bhi `oncall/` ke andar `chaos/` se import nahi kar sakta, aur
# ground-truth fields padh nahi sakta. Agar ye file wo import karti, toh agent
# ke process mein answer key load ho jaati. Phir "kya agent cheat kar raha hai?"
# ka jawab "nahi" nahi rehta, "abhi tak nahi" ban jaata — aur wo bhi ek code
# review pe tikka hota.
#
# Isliye wo coverage check `tests/test_broker.py` mein rehta hai. Tests
# `oncall/` ke bahar hain, dono taraf dekh sakte hain, aur runtime pe load
# nahi hote. Verify:
#
#     python3 -m pytest tests/test_no_shell.py -q
#
# 🔴 `command grep -rn "chaos" oncall/` se verify karna kaam **nahi** karta —
#    wo isi comment pe match kar jaata hai. Grep comment aur import mein farq
#    nahi karta. `test_no_shell.py` AST padhta hai, jahan comment maujood hi
#    nahi hota, toh wo sirf asli `import chaos` pakadta hai.


# ─────────────────────────────────────────────────────────────────────────────
# Chhote helpers — broker aur UI dono isi se poochhte hain
# ─────────────────────────────────────────────────────────────────────────────


def is_allowed(action: str) -> bool:
    """Naam allowlist mein hai ya nahi. Layer 1 ka poora kaam."""
    return action in ACTIONS


def spec_for(action: str) -> ActionSpec:
    """Action ka spec do, ya `KeyError` — caller ko handle karna hai.

    Yahan `.get()` + `None` return jaan-boojh kar nahi kiya: `None` chupchaap
    aage bah jaata hai aur galti baad mein kahin aur dikhti hai. Missing action
    ek programming error hai, missing data nahi.
    """
    return ACTIONS[action]


def remediating_actions() -> tuple[str, ...]:
    """Wo actions jo kisi fault ko theek kar SAKTE hain (docs/eval reporting ke liye).

    🔴 Naam mein "correct" jaan-boojh kar nahi hai, do wajah se:

    1. **Lint.** `CLAUDE.md` §3 ka ek rule grep-checkable hai: `oncall/` ke andar
       ground-truth field ka naam kahin nahi aana chahiye (`make lint` check #4).
       Is function ka **purana naam** us field ke naam ka plural tha — yaani grep
       ke liye ek **substring match**. Lint pehle din se red hoti, bina koi asli
       leak hone ke. Aur Makefile ka apna usool hai ki linter mein koi exception
       mechanism nahi (na `# noqa`, na `# lint-ok`), kyunki jise comment se chup
       karaya ja sake wo boundary nahi, convention hai. Toh naam badla, lint nahi.

       ⚠️ Isi wajah se ye docstring purana naam **likh bhi nahi sakti** — likhte
          hi lint dobara red ho jaati (ye galti asli mein hui, aur
          `tests/test_inject.py` ne hi pakdi). Chahiye toh git history dekho.
    2. **Semantics, jo asli wajah hai.** "Correct" ek **verdict** shabd hai, aur is
       project mein verdict sirf `oncall/oracle/health.py` deta hai. Ye function
       verdict nahi de raha — ye action ki ek **property** padh raha hai ("ye kuch
       theek kar sakta hai") na ki kisi run ka result ("isne theek kiya").

    ⚠️ `ActionSpec.purpose` ki literal value ab bhi `"correct"` hai — wahan rename
    nahi kiya, kyunki wo akela shabd kisi lint rule pe match nahi karta aur blast
    radius schema + tests tak jaata. Naam aur value ka ye farq jaan-boojh kar hai.
    """
    return tuple(n for n, s in ACTIONS.items() if s.purpose == "correct")


def distractors() -> tuple[str, ...]:
    """Wo actions jo kisi fault ka sahi jawab nahi hain."""
    return tuple(n for n, s in ACTIONS.items() if s.purpose == "distractor")
