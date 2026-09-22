"""Handlers — 9 actions ka asli kaam. Yahan Docker se baat hoti hai.

🔒 CLAUDE.md §3 ki chaar jagahon mein **doosri**.

────────────────────────────────────────────────────────────────────────────
🔒 Sabse important rule: is file mein shell nahi hai
────────────────────────────────────────────────────────────────────────────

Shell spawn karne ke chaar tareeke hote hain — ek module jo child process banata
hai, do `os` helper, aur wo ek boolean flag jo command string ko shell ke haath
mein de deta hai. In chaar mein se ek bhi `oncall/` ke andar kahin **chalta**
nahi hai.

🔴 Un chaaron ke asli naam yahan JAAN-BOOJH KAR nahi likhe. Wajah ye hai, aur ye
   is repo ka ek asli sabak hai:

   `make lint` ka check #1 ek grep hai jo poore `oncall/` tree mein wo tokens
   dhoondhta hai, aur usme koi exception mechanism nahi hai — na `# noqa`, na
   `# lint-ok`. Kyun nahi: ek linter jise ek comment likh kar chup karaya ja
   sakta hai, wo convention hai, security boundary nahi (`Makefile` ka `lint`
   target khud ye likhta hai). Iski **keemat** yahi hai — un tokens ko prose
   mein bhi nahi likh sakte, warna check apne hi documentation pe red ho jaata
   hai. Aur CLAUDE.md §2 kehta hai: "a verification command that fails is worse
   than none". Wo keemat jaan-boojh kar chuni gayi hai; `oncall/__init__.py` ka
   docstring bhi yahi karta hai.

   ⚠️ Isi wajah se grep **akela** kaafi nahi hai. Grep ko comment aur code ka
      farq nahi pata — wo sirf tab kaam karta hai jab prose khud discipline
      rakhe. Asli, authoritative check AST padhta hai, jahan comments maujood hi
      nahi hote:

          python3 tests/test_no_shell.py

   Do jagah authority: `Makefile` ka `lint` target (tokens ki list), aur
   `CLAUDE.md` §3 (rule khud).

Wajah plain English mein: agar hum command ko **ek string** bana kar shell ko
dete, toh shell us string ke andar `;`, `&&`, `|`, `$(...)` ko *meaning* de
deta. LLM ka output attacker-controlled hai (prompt injection log ke through
aa sakta hai). Ek path jaisa

    /tmp/shopapp-cache; rm -rf /var/lib/postgresql/data

string form mein **do** commands hai. List form mein wo **ek** argument hai —
ek file ka naam jo exist nahi karta, aur command bas fail ho jaati hai.

Toh har jagah list: `exec_run(["find", path, "-mindepth", "1", "-delete"])`.
Ye "escaping" nahi hai — escaping mein aap galat ho sakte ho. Ye shell ko
process se hi **hata** dena hai. Jo cheez maujood nahi, usme bug nahi hota.

────────────────────────────────────────────────────────────────────────────
🔴 Doosra rule: handler "fix hua ya nahi" ka faisla NAHI karta
────────────────────────────────────────────────────────────────────────────

`ActionResult.ok` ka matlab sirf itna hai: "command chali, exit code 0 aaya".
Iska matlab **nahi** hai "service theek ho gayi". Wo faisla sirf
`oncall/oracle/health.py` karta hai — 3 probes, 30 second, load ke neeche.

Ye seedha CLAUDE.md §0 se aata hai: "A model's opinion is never evidence."
Uska hi corollary — ek command ka exit code bhi evidence nahi hai. `docker
restart` exit 0 dega chahe service boot hote hi crash ho jaaye.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

import docker
from docker.errors import NotFound
from docker.models.containers import Container

from oncall.actions.allowlist import ACTION_NAMES

# ─────────────────────────────────────────────────────────────────────────────
# Logical service name → asli container name
# ─────────────────────────────────────────────────────────────────────────────
#
# 🔴 Ye ek explicit dict hai, `f"oncall-{service}"` nahi. Jaan-boojh kar.
#
# f-string version chhota hota, lekin wo *kisi bhi* string ko container name
# bana deta. Agar kal galti se `ALLOWED_SERVICES` mein "db" ya "oncall-db"
# ghus jaaye, f-string chupchaap us container tak pahunch jaata. Explicit dict
# mein sirf teen container reachable hain — defence in depth: do independent
# cheezein galat hone par bhi blast radius teen container se bahar nahi jaata.
#
# 🔥 `oncall-db` (agent ka apna brain) is dict mein nahi hai. Isliye is file ka
#    koi bhi handler use touch nahi kar sakta, chahe caller kuch bhi bheje.
_CONTAINERS: Mapping[str, str] = {
    "shopapp": "oncall-shopapp",
    "shopdb": "oncall-shopdb",
    "shopcache": "oncall-shopcache",
}


@dataclass(frozen=True)
class ActionResult:
    """Ek action chalne ka nateeja. 🔴 "Fault theek hua" ka dawa yahan NAHI hai."""

    ok: bool  #: command chali aur exit code 0 aaya
    detail: str  #: insaan ke padhne layak ek line (UI + DB mein jaati hai)
    exit_code: int | None = None  #: `None` = koi exec nahi hua (e.g. escalate)
    output: str = ""  #: stdout+stderr, debugging ke liye (trace mein store)


# ─────────────────────────────────────────────────────────────────────────────
# Docker helpers
# ─────────────────────────────────────────────────────────────────────────────


def _get(service: str) -> Container:
    """Logical service name se Container object.

    🔴 `_CONTAINERS[service]` jaan-boojh kar `KeyError` phenkta hai agar service
       unknown ho. Broker ka Layer 2 already service check kar chuka hota hai,
       toh yahan pahunchna matlab broker mein bug hai — aur wo bug crash ke
       roop mein dikhna chahiye, silent no-op ke roop mein nahi.
    """
    name = _CONTAINERS[service]
    try:
        return docker.from_env().containers.get(name)
    except NotFound:
        raise RuntimeError(
            f"container '{name}' not found. Did you run `make up`? "
            "Check with `docker ps -a --format '{{.Names}}'`."
        ) from None


def _exec(service: str, cmd: list[str]) -> tuple[int | None, str]:
    """Container ke andar ek command. `cmd` LIST hai — kabhi string nahi.

    🔴 `detach` yahan jaan-boojh kar **nahi** hai. `chaos/faults.py` ko wo
       chahiye tha (F3 ka lock 600 s hold karna hai), lekin action path mein
       fire-and-forget ka matlab hota "command bheji, pata nahi chali ya nahi".
       Broker ko exit code chahiye, warna wo `ActionResult.ok` jhooth bolega.
    """
    res = _get(service).exec_run(cmd)
    out = res.output.decode("utf-8", errors="replace") if res.output else ""
    return res.exit_code, out.strip()


def _psql(service: str, sql: str) -> tuple[int | None, str]:
    """shopdb ke andar `psql -c`.

    🔴 `-v ON_ERROR_STOP=1` ke bina psql pehli error ke baad bhi chalta rehta
       hai aur **exit code 0** deta hai. Uska matlab: ek fail hui action
       "success" report karti. Wo jhoothi green light hai.
    """
    return _exec(
        service,
        ["psql", "-v", "ON_ERROR_STOP=1", "-U", "shop", "-d", "shop", "-c", sql],
    )


# ═════════════════════════════════════════════════════════════════════════════
# 1/9 · restart_service — F1 ka sahi jawab
# ═════════════════════════════════════════════════════════════════════════════


def restart_service(*, service: str) -> ActionResult:
    """Container restart. SIGTERM, phir 10 s baad SIGKILL.

    ⚠️ `timeout=10` ka matlab: graceful shutdown ke liye 10 second. shopapp
       FastAPI hai — usko in-flight requests khatam karne ka mauka milna
       chahiye, warna hum khud ek naya fault (dropped requests) bana denge.
    """
    _get(service).restart(timeout=10)
    return ActionResult(True, f"{service} restarted (SIGTERM, 10 s grace)", 0)


# ═════════════════════════════════════════════════════════════════════════════
# 2/9 · start_service — F5 ka sahi jawab
# ═════════════════════════════════════════════════════════════════════════════


def start_service(*, service: str) -> ActionResult:
    """Ruke hue container ko chalu karo.

    🔴 Pehle se chalu container pe `.start()` Docker mein **error nahi** hai —
       wo no-op hai. Isliye ye idempotent hai (allowlist mein `idempotent=True`).
       Ye important hai: retry loop (max 3 attempts) same action dobara chala
       sakta hai, aur dobara chalane se naya fault nahi banna chahiye.
    """
    _get(service).start()
    return ActionResult(True, f"{service} started", 0)


# ═════════════════════════════════════════════════════════════════════════════
# 3/9 · stop_service — distractor. Kabhi sahi jawab nahi.
# ═════════════════════════════════════════════════════════════════════════════


def stop_service(*, service: str) -> ActionResult:
    """Container band karo. Ye kisi fault ka fix nahi hai — ye rope hai.

    Handler asli hai (fake nahi) kyunki agar LLM ye chun le, toh loop ko sach
    dikhana chahiye: action chala, aur oracle ne fail kiya. Fake handler rakhna
    result ko sundar bana deta aur measurement ko jhootha.
    """
    try:
        _get(service).stop(timeout=5)
    except Exception as exc:  # noqa: BLE001
        # Already-stopped pe Docker 409 deta hai. Distractor hone ke NAATE bhi
        # ye action ASLI hai — crash (worker retry-loop) behtar nahi hai clean
        # exit-0 + oracle-fail. Oracle hi faisla karega ki madad hui ya nahi.
        if "not running" in str(exc):
            return ActionResult(True, f"{service} was already stopped", 0)
        raise
    return ActionResult(True, f"{service} stopped", 0)


# ═════════════════════════════════════════════════════════════════════════════
# 4/9 · clear_directory — F2 ka sahi jawab
# ═════════════════════════════════════════════════════════════════════════════


def clear_directory(*, service: str, path: str) -> ActionResult:
    """Directory ke andar ka sab kuch delete karo, directory khud rakho.

    🔴 `find <path> -mindepth 1 -delete` kyun, `rm -rf <path>/*` kyun nahi:

       (a) `*` ko **shell** expand karta hai. Shell hai hi nahi. `rm -rf` ko
           list form mein `["rm","-rf","/tmp/x/*"]` dena literally ek file
           dhoondhta hai jiska naam `*` hai — kuch delete nahi hota, aur exit
           code 0 aa jaata hai. Ek chupchaap no-op.
       (b) `-mindepth 1` khud directory ko bachata hai. Agar directory hi
           delete ho jaaye, toh shopapp ka `CACHE_DIR` gayab — aur wo F2 se
           bhi bada fault hai (mount point tut jaata hai).
       (c) `-delete` dotfiles bhi utha leta hai. `*` unhe chhod deta hai, aur
           `.health-probe` ek dotfile hai (app.py ka `_PROBE_NAME`).
    """
    code, out = _exec(service, ["find", path, "-mindepth", "1", "-delete"])
    ok = code == 0
    return ActionResult(ok, f"cleared {service}:{path}" if ok else f"clear fail: {out}", code, out)


# ═════════════════════════════════════════════════════════════════════════════
# 5/9 · kill_blocking_queries — F3 ka sahi jawab
# ═════════════════════════════════════════════════════════════════════════════

#: 🔴 Ye SQL teen jagah careful hai:
#:
#: 1. **Dono taraf se dhoondhta hai** (UNION). Sirf `pg_blocking_pids()` pe
#:    bharosa karna bug tha: wo function tabhi kuch deta hai jab koi session
#:    *abhi wait kar raha ho*. F3 mein pool idle ho sakta hai us pal — koi
#:    waiter nahi, toh koi blocker nahi mila, toh 0 rows terminate, toh action
#:    "success" (exit 0) aur fault zinda. Isliye branch (a): `pg_locks` se
#:    seedha poochho ki kaun ek user table pe AccessExclusiveLock **hold** kar
#:    raha hai — waiter ho ya na ho.
#:
#: 2. Sirf `AccessExclusiveLock`. Wahi ek mode hai jo plain `SELECT` ko block
#:    karta hai. Normal INSERT ka `RowExclusiveLock` bhi maar dete toh hum
#:    healthy traffic kill kar rahe hote — fix ke naam pe naya outage.
#:
#: 3. `query LIKE '%pg_sleep%'` jaisa pattern jaan-boojh kar **nahi** hai.
#:    Wo F3 ke exact chaos command ko pehchanta — yaani agent answer key dekh
#:    raha hota (CLAUDE.md §3: ground truth agent ke liye invisible hai).
#:    Lock mode se reason karna generic SRE logic hai; string match cheating.
_KILL_BLOCKERS_SQL = """
WITH blockers AS (
    SELECT DISTINCT l.pid
    FROM pg_locks l
    JOIN pg_class c ON c.oid = l.relation
    WHERE l.locktype = 'relation'
      AND l.granted
      AND l.mode = 'AccessExclusiveLock'
      AND c.relnamespace = 'public'::regnamespace
    UNION
    SELECT DISTINCT unnest(pg_blocking_pids(pid))
    FROM pg_stat_activity
    WHERE wait_event_type = 'Lock'
)
SELECT pg_terminate_backend(pid) FROM blockers WHERE pid <> pg_backend_pid();
"""


def kill_blocking_queries(*, service: str) -> ActionResult:
    """Jo session dusron ka raasta rok rahe hain, unhe terminate karo.

    ⚠️ `pg_terminate_backend` SIGTERM bhejta hai us backend ko — uska
       transaction rollback ho jaata hai. F3 mein wo transaction ne kuch likha
       hi nahi tha (sirf `LOCK` + `pg_sleep`), toh data loss zero hai. Lekin
       ye general baat nahi hai: agar koi asli long-running write chal rahi
       hoti, wo rollback ho jaati. Isliye ye action approval gate ke peeche hai.
    """
    code, out = _psql(service, _KILL_BLOCKERS_SQL)
    ok = code == 0
    # psql `SELECT` ka output "(N rows)" hota hai — kitne mare, wo trace mein
    # rehta hai. 0 rows bhi exit 0 deta hai, aur wo theek hai: "koi blocker
    # nahi tha" ek valid nateeja hai. Fault gaya ya nahi — wo oracle bataayega.
    return ActionResult(ok, f"{service}: blocking sessions terminated" if ok else f"psql fail: {out}", code, out)


# ═════════════════════════════════════════════════════════════════════════════
# 6/9 · set_db_setting — F4 ka sahi jawab
# ═════════════════════════════════════════════════════════════════════════════


def set_db_setting(*, service: str, key: str, value: int) -> ActionResult:
    """`ALTER SYSTEM SET <key> = <value>`, phir container restart.

    🔴 Yahan ek asli SQL-injection risk hai, aur wo R5 mein record hai:

       `ALTER SYSTEM` **bind parameters accept nahi karta**. Yaani hum
       `ALTER SYSTEM SET max_connections = %s` nahi likh sakte — Postgres us
       syntax ko utility statement ke liye mana karta hai. Toh value string
       mein hi jaani padegi. Interpolation majboori hai, choice nahi.

       Do cheezein isse safe banati hain, aur dono zaruri hain:
         (a) `key` allowlist se aati hai (`ALLOWED_DB_KEYS`) — LLM ka bheja
             hua koi bhi doosra naam broker Layer 3 pe mar jaata hai.
         (b) `value` ko yahan `int()` kiya jaata hai. Ek int ka `str()` kabhi
             SQL nahi ban sakta — usme quote, semicolon, space kuch nahi hota.

       Sirf (a) kaafi nahi tha, sirf (b) bhi nahi. Dono ek saath hi is line ko
       safe banate hain.

    🔴 Restart kyun, reload kyun nahi: `max_connections` ek `postmaster`-level
       GUC hai. Uska naya value shared memory ke size ko badalta hai, aur wo
       sirf boot pe allocate hoti hai. `pg_reload_conf()` chupchaap purana
       value hi rakhta hai — reload karke "fix ho gaya" maan lena F4 mein
       sabse aasaan jhoothi jeet hai.
    """
    ivalue = int(value)  # (b) — bind param nahi mil raha, toh type se guard
    code, out = _psql(service, f"ALTER SYSTEM SET {key} = {ivalue};")
    if code != 0:
        return ActionResult(False, f"ALTER SYSTEM fail: {out}", code, out)
    _get(service).restart(timeout=10)
    return ActionResult(True, f"{service}: {key} = {ivalue} set, then restarted", 0, out)


# ═════════════════════════════════════════════════════════════════════════════
# 7/9 · flush_cache — distractor
# ═════════════════════════════════════════════════════════════════════════════


def flush_cache(*, service: str) -> ActionResult:
    """Redis pe `FLUSHALL`. Kisi fault ka fix nahi.

    F5 ke liye tempting hai ("cache down → cache saaf karo") aur wahi uski
    khoobi hai: F5 mein shopcache chal hi nahi raha, toh `redis-cli` connect
    hi nahi kar payega aur ye action **exit code non-zero** ke saath fail
    hogi. Ek galat action jo saaf-saaf fail hoti hai, wo trace mein achha
    evidence hai.
    """
    code, out = _exec(service, ["redis-cli", "FLUSHALL"])
    ok = code == 0
    return ActionResult(ok, f"{service}: FLUSHALL executed" if ok else f"redis-cli fail: {out}", code, out)


# ═════════════════════════════════════════════════════════════════════════════
# 8/9 · vacuum_table — distractor (F3 ka sabse tempting galat jawab)
# ═════════════════════════════════════════════════════════════════════════════


def vacuum_table(*, service: str, table: str) -> ActionResult:
    """`VACUUM (ANALYZE) <table>`. F3 ke liye tempting, aur ghalat.

    🔴 `SET lock_timeout` pehle kyun bheja jaata hai:

       `VACUUM` ko table pe `ShareUpdateExclusiveLock` chahiye. F3 mein us
       table pe already `AccessExclusiveLock` hai, toh VACUUM **queue mein lag
       jaata hai** aur 600 second (F3 ka `pg_sleep`) tak wait karta hai. Bina
       timeout, ye handler 10 minute block ho jaata — aur uske saath broker,
       worker, aur poora incident. Ek distractor ko fail hona chahiye, hang
       nahi.

       5 second baad Postgres `55P03 lock_not_available` deta hai, psql
       non-zero exit karta hai (`ON_ERROR_STOP=1`), aur hume ek saaf failure
       milta hai jo trace mein padhne layak hai.

    🔴 `table` bhi interpolate ho raha hai, `ALTER SYSTEM` wali hi wajah se —
       `VACUUM` utility statement hai, bind params nahi leta. Guard wahi hai:
       `ALLOWED_TABLES` (aaj usme sirf `orders` hai).
    """
    sql = f"SET lock_timeout = '5s'; VACUUM (ANALYZE) {table};"
    code, out = _psql(service, sql)
    ok = code == 0
    return ActionResult(ok, f"{service}: VACUUM {table} executed" if ok else f"VACUUM fail: {out}", code, out)


# ═════════════════════════════════════════════════════════════════════════════
# 9/9 · escalate — terminal. Kuch nahi chalata.
# ═════════════════════════════════════════════════════════════════════════════


def escalate(*, reason: str) -> ActionResult:
    """Insaan ko bulao. Koi Docker call nahi.

    🔴 `exit_code=None` — `0` nahi. Farq matter karta hai: `0` ka matlab
       "command chali aur pass hui". Yahan koi command chali hi nahi. `None`
       likhne se trace mein saaf dikhta hai ki ye ek decision tha, execution
       nahi. Agar hum `0` likhte, toh metrics mein escalate ek "successful
       action" ban jaata aur success rate phool jaata.
    """
    return ActionResult(True, f"escalated to a human: {reason}", None)


# ─────────────────────────────────────────────────────────────────────────────
# 🔒 HANDLERS — broker isi dict se dispatch karta hai
# ─────────────────────────────────────────────────────────────────────────────
#
# 🔴 Dispatch dict se hota hai, `getattr(module, action)` se nahi.
#
#    `getattr` chhota hota, lekin wo *poore module* ko callable bana deta.
#    `action="_psql"` ya `action="_get"` bhi kaam kar jaata — helper functions
#    bhi reachable ho jaate. Dict mein sirf ye 9 naam maujood hain; baaki sab
#    KeyError hai. Ye wahi allowlist-vs-denylist argument hai, ek level neeche.
HANDLERS: Mapping[str, Callable[..., ActionResult]] = {
    "restart_service": restart_service,
    "start_service": start_service,
    "stop_service": stop_service,
    "clear_directory": clear_directory,
    "kill_blocking_queries": kill_blocking_queries,
    "set_db_setting": set_db_setting,
    "flush_cache": flush_cache,
    "vacuum_table": vacuum_table,
    "escalate": escalate,
}

# 🔴 `assert` nahi (python -O use hata deta hai) — explicit raise.
if set(HANDLERS) != set(ACTION_NAMES):  # pragma: no cover - import-time
    raise RuntimeError(
        "handlers.py allowlist.py se drift ho gaya.\n"
        f"  handler hai par allowlist mein nahi : {sorted(set(HANDLERS) - set(ACTION_NAMES))}\n"
        f"  allowlist mein hai par handler nahi : {sorted(set(ACTION_NAMES) - set(HANDLERS))}\n"
        "CLAUDE.md §3: chaar jagah — allowlist, handlers, schema ka Literal, test_broker."
    )
