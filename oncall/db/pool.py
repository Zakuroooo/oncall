"""Connection pool — control plane ka Postgres access, ek hi jagah se.

─────────────────────────────────────────────────────────────────────────────
🔴 YE FILE KYUN EXIST KARTI HAI (ek line mein)
─────────────────────────────────────────────────────────────────────────────
Har incident 3 tables mein likhta hai (`incidents`, `events`, `verdicts`) aur
worker + API + reaper — teen alag jagah se. Agar har jagah apna
`psycopg.connect(...)` karti, toh (a) har call pe naya TCP + auth handshake
(~5–20 ms bekaar), aur (b) leak hone pe koi ek jagah nahi jahan se pata chale.

Pool dono theek karta hai: connections reuse hote hain, aur "kitne khule hain"
ek hi object ka sawaal ban jaata hai.

─────────────────────────────────────────────────────────────────────────────
🔥 NAAM KA TAKRAAV — `POOL_MAX` yahan JAAN-BOOJH KAR NAHI HAI
─────────────────────────────────────────────────────────────────────────────
`POOL_MAX = 10` is project mein pehle se maujood hai — `stack/shopapp/app.py:111`
mein — aur wo number **F4 ka aadha mechanism** hai:

    POOL_MIN = 1  → idle pe 1 connection  → `max_connections=5` ke andar → 200 ✅
    POOL_MAX = 10 → load pe 10 maangta    → 6th pe `too many clients`  → 500 🔴

Yaani `POOL_MAX` ek *measurement instrument* hai, capacity setting nahi. Usko
badalna N1 (naive-vs-sustained disagreement) ko badal deta hai.

Isliye control plane ka apna number **`CONTROL_POOL_MAX`** kehlata hai, `POOL_MAX`
nahi. Faayda ek grep se dikhta hai — par **assignment** wala grep, naam wala nahi:

    grep -rnE '^[A-Za-z_]*POOL_(MAX|MIN) *=' --include="*.py" .

✅ 2026-08-22 pe measure kiya — theek 4 line:

    stack/shopapp/app.py:110:POOL_MIN = 1        ← victim, F4 ka math
    stack/shopapp/app.py:111:POOL_MAX = 10       ← victim, F4 ka math
    oncall/db/pool.py:146:CONTROL_POOL_MIN = 1   ← control plane
    oncall/db/pool.py:150:CONTROL_POOL_MAX = 4   ← control plane

🔴 Pehle yahan `grep -rn "POOL_MAX" --include="*.py" .` likha tha, is daawe ke
   saath ki wo "sirf victim stack" deta hai. **Wo galat tha** — measure karne pe
   wo 30 line deta hai, kyunki `chaos/faults.py`, `oncall/oracle/load.py`,
   `allowlist.py` aur `tests/test_schema.py` sab F4 ka mechanism *samjhaate*
   hain. Prose bhi match hoti hai. `= ` wala anchor **assignment** dhoondta hai,
   zikr nahi — aur "naam ka maalik kaun hai" ka sawaal assignment ka hai.
   (`CLAUDE.md` §2: command pehle chalao, phir usko saboot bana kar likho.)

Agar dono ka naam ek hota, toh koi "pool thoda bada kar do" sochkar galat file
chhed sakta tha — aur project ka headline result chup-chaap badal jaata.
🔴 **Load-bearing constant ko uske naam pe monopoly milni chahiye.** (D50)

─────────────────────────────────────────────────────────────────────────────
⚠️ `CONTROL_POOL_MAX = 4` — ye ek anumaan hai, measurement nahi
─────────────────────────────────────────────────────────────────────────────
Control plane ke asli concurrent DB users: API ke 2–3 handler (UI list/detail/
stats), queue worker ka job claim, reaper ka periodic sweep. Peak ~4.

Chhota pool jaan-boojh kar: agar kahin connection leak hua, 4 ka pool usko
**seconds mein timeout** banakar dikha deta hai. 20 ka pool usi leak ko ghanton
tak chhupa leta hai. Chhoti limit ek diagnostic hai.

Tripwire: logs mein `PoolTimeout` dikhe → number badhao, aur wahi observation
is anumaan ka falsifier hai (`docs/RISKS.md`).

─────────────────────────────────────────────────────────────────────────────
SYNC pool, ASYNC nahi — aur ye D24 ka seedha nateeja hai
─────────────────────────────────────────────────────────────────────────────
FastAPI mein `def` handler (async nahi) ek **threadpool** mein chalta hai. Matlab
usme blocking psycopg call event loop ko rok nahi sakti. Isliye humein `await`
ka poora tax dene ki zarurat nahi — 7 tables, hand-written SQL, ek user.

Ek hi exception: SSE stream (`routes_stream.py`) `async def` hai, kyunki wo
minuton tak khula rehta hai. Wo pool ko **seedha touch nahi karta** — poll
`asyncio.to_thread(...)` ke through hota hai, toh connection sirf query ke
dauraan borrow hoti hai, poore stream ke liye nahi. (Warna ek khula browser tab
poora pool khaa jaata.)

─────────────────────────────────────────────────────────────────────────────
Related
─────────────────────────────────────────────────────────────────────────────
| Cheez | Kahan |
|---|---|
| Kyun psycopg 3, asyncpg nahi | `docs/DECISIONS.md` D24 |
| Naam ka takraav + ye file | `docs/DECISIONS.md` D50 |
| Schema kaun banata hai | `oncall/db/migrate.py` |
| App ka SQL | `oncall/db/queries.py` (metrics ka alag — CODEMAP §3g) |
"""

from __future__ import annotations

import atexit
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from oncall.config import load_settings

if TYPE_CHECKING:  # sirf type checker ke liye — runtime pe import nahi hota
    from psycopg import Connection


class _NoPsycopg(Exception):
    """Placeholder jab `psycopg` install na ho.

    🔴 Kaam sirf itna: neeche `except PoolTimeout:` wali line COMPILE ho jaaye
       jab asli `PoolTimeout` maujood na ho. `PoolTimeout = Exception` likhna
       galat hota — wo *har* exception ko pool-timeout maan leta.
    """


# ─────────────────────────────────────────────────────────────────────────────
# psycopg OPTIONAL hai — `chaos/faults.py` ke `_NoDockerSDK` jaisa hi pattern
# ─────────────────────────────────────────────────────────────────────────────
# Wajah practical hai: is machine pe `pip install` possible nahi (network sirf
# do host allow karta hai), lekin `tests/test_schema.py` aaj chalni chahiye. Wo
# test schema ka *text* padhta hai — usko database ki zarurat nahi, sirf import
# ka kaam karna zaroori hai.
#
# Hard import hota toh `python -m pytest tests/test_schema.py` ek
# `ModuleNotFoundError` pe mar jaata — yaani wo check jo bina DB ke chal sakta
# tha, wo bhi DB ke intezaar mein ruk jaata. Ulta hai.
#
# Asli connection maangne pe `get_pool()` ek saaf message deta hai, raw
# traceback nahi.
try:
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool, PoolTimeout

    _HAS_PSYCOPG = True
except ModuleNotFoundError:  # pragma: no cover - install state pe depend karta hai
    dict_row = None  # type: ignore[assignment]
    ConnectionPool = None  # type: ignore[assignment,misc]
    PoolTimeout = _NoPsycopg  # type: ignore[misc,assignment]
    _HAS_PSYCOPG = False


# ═════════════════════════════════════════════════════════════════════════════
# 0. Do number — dono hardcoded, env se nahi
# ═════════════════════════════════════════════════════════════════════════════
# Env var isliye nahi ki ye ek *contract* hai, tuning knob nahi. Agar ye
# `ONCALL_POOL_MAX` hota, toh ek measurement run doosre se alag pool pe chal
# sakta tha — aur logs mein wo farq dikhta bhi nahi. Wahi wajah
# `ORACLE_PROBES = 3` ke hardcoded hone ki hai (`oncall/config.py:79`).

#: Idle pe kitne connection khule rehte hain. 1 = ek gaddi taiyaar, baaki
#: on-demand. 0 rakhne se pehli request pe handshake ka ~10 ms lag jaata.
CONTROL_POOL_MIN = 1

#: 🔴 `POOL_MAX` NAHI — dekho is file ka header. Ye capacity hai, F4 ka
#: mechanism nahi. ⚠️ 4 ek anumaan hai (2026-08-22), measurement nahi.
CONTROL_POOL_MAX = 4

#: Pool se connection maangne pe kitna intezaar. 5s = `ORACLE_PROBE_TIMEOUT_S`
#: ke barabar jaan-boojh kar: agar oracle ka probe 5s mein timeout hota hai, toh
#: uska DB write bhi usi paimaane pe fail hona chahiye — warna probe timeout ke
#: baad ek zombie write 30s tak latki rehti hai.
POOL_ACQUIRE_TIMEOUT_S = 5.0


# ═════════════════════════════════════════════════════════════════════════════
# 1. Singleton — LAZY, import-time pe nahi
# ═════════════════════════════════════════════════════════════════════════════
# Yahi wajah hai jo `app factory` ke peeche bhi hai (D49): import ka koi
# side-effect nahi hona chahiye. Agar pool module-level pe bana dete, toh
# `import oncall.db.pool` ek TCP connection khol deta — matlab `--help`, ek
# unit test, ya ek galat-DSN wali typo bhi ek network attempt ban jaati.
#
# `_lock` isliye ki queue worker aur reaper do alag thread ho sakte hain, aur
# dono pehli baar `get_pool()` maar sakte hain. Bina lock, do pool ban jaate —
# aur "kitne connection khule hain" wala poora faayda khatam.

_pool: Any = None
_lock = threading.Lock()


def get_pool() -> Any:
    """Ek hi `ConnectionPool` — pehli call pe banta hai, uske baad wahi."""
    global _pool
    if _pool is not None:
        return _pool

    if not _HAS_PSYCOPG:
        # 🔴 `SystemExit` NAHI. Ye library code hai — yahan process marna
        #    caller ka faisla chura lena hai. Ek asli exception raise karo.
        raise RuntimeError(
            "psycopg install nahi hai — `make install` chalao. "
            "(Pool ke bina DB access possible nahi; text-only checks "
            "jaise tests/test_schema.py bina iske chalte hain.)"
        )

    with _lock:
        # Do baar check (double-checked locking): pehla check lock ke bahar
        # speed ke liye, doosra lock ke andar sach ke liye. Bina doosre check,
        # do thread lock ke liye line mein lag sakte the aur dono pool banate.
        if _pool is None:
            dsn = load_settings().oncall_db_url
            _pool = ConnectionPool(
                dsn,
                min_size=CONTROL_POOL_MIN,
                max_size=CONTROL_POOL_MAX,
                timeout=POOL_ACQUIRE_TIMEOUT_S,
                kwargs={"row_factory": dict_row},
                # psycopg-pool 3.2 constructor-time open pe warning deta hai
                # (default badal raha hai), toh explicitly neeche kholte hain.
                open=False,
            )
            # 🔥 `wait=False` — ye load-bearing hai, style nahi.
            #    `wait=True` ka matlab: pool bharne ka intezaar karo. Agar
            #    oncall-db down hai, toh control plane START HI NAHI HOTA — aur
            #    "DB down hai" ek "app nahi chala" ki tarah dikhta hai, jo ek
            #    alag bug jaisa lagta hai. `wait=False` ke saath process zinda
            #    rehta hai aur uska apna `/health` sach bol paata hai.
            _pool.open(wait=False)
    return _pool


# ═════════════════════════════════════════════════════════════════════════════
# 2. `get_conn()` — poore project ka EK entry point DB tak
# ═════════════════════════════════════════════════════════════════════════════
@contextmanager
def get_conn() -> Iterator[Connection]:
    """Pool se ek connection lo, kaam karo, wapas de do.

    Use aise hota hai:

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT state FROM incidents WHERE id = %s", (iid,))
                row = cur.fetchone()      # dict hai, tuple nahi
                state = row["state"]

    🔴 `row["state"]`, `row[3]` nahi — `kwargs={"row_factory": dict_row}` ki
       wajah se. Positional index tab tootta hai jab koi beech mein column jod
       de, aur wo tootna **chup-chaap** hota hai: `row[3]` ab kisi doosre column
       ka data deta hai, error nahi. Naam se index karna us poori class ko
       khatam kar deta hai.

    🔴 COMMIT ka zikr yahan nahi hai — jaan-boojh kar. psycopg **3** mein
       connection ka context manager exit pe COMMIT karta hai (exception pe
       ROLLBACK). psycopg 2 mein aisa nahi tha. Isliye is codebase mein koi
       explicit `conn.commit()` nahi milega; agar kahin dikhe, wo psycopg-2 ki
       aadat hai (`docs/TEACH_PHASE1.md` §2h).
    """
    pool = get_pool()
    try:
        with pool.connection() as conn:
            yield conn
    except PoolTimeout as exc:  # pragma: no cover - live pool chahiye
        # Message mein number likho, sirf "timeout" nahi — warna debug karte
        # waqt pata nahi chalta ki limit 4 thi ya 40.
        raise RuntimeError(
            f"Pool se {POOL_ACQUIRE_TIMEOUT_S}s mein connection nahi mila "
            f"(CONTROL_POOL_MAX={CONTROL_POOL_MAX}). Ya load asli hai, ya "
            f"kahin connection leak ho rahi hai — dono ka pehla check "
            f"`SELECT count(*) FROM pg_stat_activity` hai."
        ) from exc


def close_pool() -> None:
    """Shutdown pe pool band karo. Idempotent — do baar call safe hai."""
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None


# 🔥 `atexit` isliye, aur ye ek asli bug rokta hai.
#
# Agar cleanup na ho, toh process marne ke baad bhi uske connections Postgres
# mein TCP timeout tak (~30s) zinda rehte hain. Ye project restart-heavy hai —
# F1 ka fix hi restart hai, aur ablation harness ek raat mein 200 incident
# chalata hai. Har run 4 orphan connection chhodta, aur kuch runs baad
# oncall-db khud `too many clients` dene lagta.
#
# 🔴 Wo bilkul F4 jaisa dikhta — lekin wo mera bug hota, fault nahi. Aur usko
#    debug karne mein poora din jaata, kyunki symptom asli fault se identical
#    hai. Do line usko exist hone hi nahi deti.
atexit.register(close_pool)


# ═════════════════════════════════════════════════════════════════════════════
# 3. `ping()` — control plane ke apne `/health` ke liye
# ═════════════════════════════════════════════════════════════════════════════
def ping() -> tuple[bool, str]:
    """oncall-db zinda hai? `(ok, reason)` lautata hai, exception nahi phenkta.

    🔴 Exception nahi, kyunki iska caller `GET /health` hai. Ek health endpoint
       jo khud 500 de deta hai, wo apna hi kaam nahi karta — usse pata nahi
       chalta ki *kya* toota hai. `(False, "…")` se `/health` 503 ke saath
       wajah bhi bol sakta hai.

    ⚠️ `SELECT 1` jaan-boojh kar — ye batata hai "connection ban rahi hai".
       Schema maujood hai ya nahi, wo alag sawaal hai aur uska jawaab
       `oncall/db/migrate.py:verify()` deta hai. Ek check, ek dawa.
    """
    if not _HAS_PSYCOPG:
        return False, "psycopg install nahi hai (`make install`)"
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            cur.fetchone()
        return True, "ok"
    except Exception as exc:  # noqa: BLE001 - health check ko sab pakadna hai
        # 🔴 Hum khud DSN message mein NAHI daalte — DSN mein password hota hai
        #    aur ye string `/health` ke response mein browser tak jaati hai.
        #    Sirf exception ka class + uska message.
        # ⚠️ Ye psycopg ke apne message pe bharosa karta hai (2026-08-22 tak
        #    hand-verify nahi hua ki wo DSN echo nahi karta). Pehla live run pe
        #    check karna hai; agar karta hai, yahan filter lagega.
        return False, f"{type(exc).__name__}: {exc}"
