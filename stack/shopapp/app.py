# ─────────────────────────────────────────────────────────────────────────────
# shopapp — the monitored service. Ye OnCall ka "patient" hai.
#
# 🔴 Ye file jaan-boojh kar chhoti hai. Ye ek asli e-commerce app nahi hai —
#    ye ek aisi service hai jiska `/health` endpoint asli dependencies pe
#    depend karta hai, taaki jab wo dependencies toot jaayein toh health
#    genuinely fail ho. Business logic minimum hai.
#
# 🔴 Sabse important design decision is poori file mein:
#
#       /health ek asli DB connection leta hai aur ek asli query chalata hai.
#
#    Alternative jo reject hua: `return {"status": "ok"}`. Wo standard practice
#    hai aur wo is project ko IMPOSSIBLE bana deta. Kyun: agar /health kuch
#    check nahi karta, toh wo hamesha 200 dega — chahe database mar chuka ho.
#    Phir "kya fix kaam kiya?" ka jawab hamesha "haan" hoga, aur poora health
#    oracle ek jhoothi machine ban jaata.
#
#    Iska ulta khatra bhi hai aur wo asli hai: aisa /health ek DDoS amplifier
#    ban sakta hai (har health check ek DB query). Isliye ye service SIRF
#    127.0.0.1 pe bind hai (docker-compose.yml) aur ye ek disposable test
#    fixture hai, production service nahi.
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
import os
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import psycopg  # noqa: F401 — dekho file ke aakhir ka note
import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException
from psycopg_pool import AsyncConnectionPool

# ─────────────────────────────────────────────────────────────────────────────
# 🔴 Ye teen lines OnCall ke liye INPUT banati hain, is app ke liye nahi.
#
# psycopg ka pool apni connection failures ko `logging` ke through bolta hai —
# logger ka naam `psycopg.pool`, level WARNING:
#
#     "error connecting in <pool>: connection failed: FATAL: sorry, too many
#      clients already"
#
# ✅ Ye string psycopg ke source mein `_add_connection()` ke andar hai
#    (`logger.warning("error connecting in %r: %s", ...)`).
#
# Ye line F4 ke liye SONA hai. F4 mein shopapp ko jo dikhta hai wo sirf ek
# `PoolTimeout` hai — "connection nahi mila" — jo diagnosis ke liye bekaar hai.
# Asli wajah (`too many clients`) sirf is warning mein aur shopdb ke logs mein
# hai. Aur OnCall ka log collector `docker logs` padhta hai, yaani stderr.
#
# Python ka default behaviour: bina handler ke WARNING `logging.lastResort` se
# stderr pe chala jaata hai — toh ye technically kaam kar jaata. Lekin uspe
# bharosa nahi kar rahe: `lastResort` ek fallback hai, feature nahi, aur usme
# timestamp bhi nahi hota. `basicConfig` se ek asli handler + format milta hai.
#
# ⚠️ uvicorn apne loggers ko `propagate: False` ke saath configure karta hai,
#    toh ye root handler uvicorn ke access logs ko DOBARA print nahi karega.
#    Verify karne ka tareeka: `docker logs oncall-shopapp | grep -c "GET /health"`
#    — har request ek hi baar dikhni chahiye, do baar nahi.
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

DB_URL = os.environ.get("DATABASE_URL", "postgresql://shop:shop@shopdb:5432/shop")
REDIS_URL = os.environ.get("REDIS_URL", "redis://shopcache:6379/0")

# ─────────────────────────────────────────────────────────────────────────────
# 🔴 Ye do numbers F4 fault ka poora mechanism hain. Inhe badalne se project ka
#    headline result badal jaata hai, isliye ye yahan comment ke saath hain.
#
#    POOL_MIN = 1  → idle pe pool sirf 1 connection rakhta hai. Isliye restart
#                    ke baad ek AKELA /health probe pass ho jaata hai.
#    POOL_MAX = 10 → load pe pool 10 connections tak badhta hai.
#
# F4 chaos shopdb ka `max_connections` 100 se 5 pe le jaata hai
# (`chaos/faults.py`, `_F4_BROKEN_MAX_CONN`). Toh:
#
#       1 request   → pool 1 connection maangta    → 200 OK
#      20 requests  → pool 10 tak jaana chahta hai → 6th connection pe Postgres
#                     `FATAL: sorry, too many clients already` deta hai
#
# 🔴 5 kyun, 2 nahi? Yahan pehle "2" likha tha aur wo GALAT tha. shopdb
#    `postgres:15-alpine` hai, aur PG15 ka `superuser_reserved_connections`
#    default 3 hai — manual bolti hai: *"The value must be less than
#    max_connections."* `max_connections=2` pe ye 3 < 2 maangta hai, jo jhooth
#    hai → Postgres START HI NAHI hota. Phir F4 "database down" ban jaata
#    (F2 ki nakal), "pool exhausted" nahi — galat fault, galat diagnosis.
#    ✅ postgresql.org/docs/15/runtime-config-connection.html (2026-08-22)
#
# ⚠️ App ko jo exception dikhta hai wo `TooManyConnections` NAHI hai.
#    psycopg_pool waiting client ko queue karta hai, aur `timeout` khatam hone
#    pe `PoolTimeout` uthata hai ("couldn't get a connection after 3.00 sec").
#    Asli `FATAL: sorry, too many clients already` do jagah milta hai:
#    (1) shopdb ke container logs mein, aur (2) shopapp ke logs mein us
#    `psycopg.pool` WARNING ke through jo upar configure kiya. Dono `docker
#    logs` se aate hain — isliye OnCall ka input container logs hai, HTTP
#    response body nahi.
#
#    Wahi naive-vs-sustained disagreement project ka headline result hai
#    (METRICS.md N1).
# ─────────────────────────────────────────────────────────────────────────────
POOL_MIN = 1
POOL_MAX = 10

# ─────────────────────────────────────────────────────────────────────────────
# 🔥 F4 ka asli mechanism YAHIN complete hota hai (2026-08-24, measured).
#
# Pehla live oracle-test ek uncomfortable sach laaya: 20 rps burst-load mein
# SAARI requests 200 return karti thin, chahe max_connections=5 ho. Kyun:
# psycopg_pool server-refusal (6th connection FATAL) pe naye connections ki
# GROWTH rok deta hai lekin requests ko queue karta rehta hai — aur ~5ms ki
# query ke saath 20-deep queue ~100ms mein drain ho jaati hai, 3s timeout se
# kabhi nahi takraati. Yaani "6th connection pe 503" wali kahani HTTP pe
# dikhti hi nahi thi — sirf shopdb ke logs mein FATAL likha jaata tha.
#
# Ilaaj Little's law hai: queue tabhi timeout tak pahunchti hai jab
#     concurrency-need = arrival_rate x service_time  >  available_connections
# Healthy (cap 100): 20 rps x 0.30 s = 6 conns maangte hain <= POOL_MAX 10 -> sab 200.
# F4 (cap 5):        6 > 5 -> queue bina-ruki badhti hai -> PoolTimeout -> 503s
#                    load window ke andar -> sustained oracle FAIL (sahi!).
# Restart-only "fix": pool khali hota hai, IDLE probe 200 deti hai (naive PASS,
#                     false-positive) lekin load wapas aate hi 6>5 phir se.
#
# 0.30 s ek realistic list-orders latency hai (asli e-commerce APIs 50-300ms),
# sirf demo-speed nahi. Default 0 — compose ise 0.3 set karta hai.
# ─────────────────────────────────────────────────────────────────────────────
ORDER_SLEEP_S = float(os.environ.get("ORDER_SLEEP_S", "0"))

# Health check ki queries pe timeout. Iske bina ek exhausted pool pe request
# hamesha ke liye latak jaati hai aur 500 ki jagah timeout milta hai — do
# alag failure modes, aur oracle ko clear signal chahiye.
HEALTH_TIMEOUT_S = 3.0

# ─────────────────────────────────────────────────────────────────────────────
# 🔴 F2 (disk full) ka detection point. Ye teen lines woh cheez hain jinke bina
#    F2 fault **detect hi nahi hota** — aur ek fault jo detect nahi hota, wo
#    measure bhi nahi hota.
#
# CACHE_DIR ek size-capped tmpfs hai (docker-compose.yml, 16 MiB). Chaos F2 usme
# `dd` se bada file likh ke usko bhar deta hai. Us halat mein is app ka koi bhi
# cache write `OSError: [Errno 28] No space left on device` deta hai.
#
# 🔴 Ye env var se NAHI aata — hardcoded hai. Wajah: yahi path
#    `oncall/actions/allowlist.py` ke `ALLOWED_PATHS` mein bhi hai. Agar ye env
#    se badal sakta, toh koi shopapp ko `/var/lib/postgresql/data` pe point
#    karke allowlist ko bypass kar sakta tha — clear_directory ka target
#    allowlist mein hai, lekin app ka assumption uske bahar chala jaata.
#    Do jagah likhi hui ek constant, jaan-boojh kar, security ke liye.
CACHE_DIR = Path("/tmp/shopapp-cache")

# Probe file ka naam. Leading dot: `find -mindepth 1 -delete` (F2 ka heal) ise
# bhi uda dega, aur wo theek hai — probe har baar naya banta hai.
_PROBE_NAME = ".health-probe"
# ─────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Pool aur Redis client ek baar banao, process ke saath jiyo.

    Kyun lifespan aur per-request connection nahi: per-request `psycopg.connect()`
    har request pe TCP + auth ka round trip karta hai (~5-15 ms), aur — zyada
    important — wo pool ke bina chalta hai, toh POOL_MAX ka concept hi khatam
    ho jaata. F4 fault ko pool chahiye.

    🔴 `open=False` + explicit `await pool.open()`: psycopg-pool 3.2 warning
       deta hai agar pool constructor ke andar khul jaaye. Aur `wait()` NAHI
       kar rahe — kyunki agar shopdb down hai toh app ko phir bhi start hona
       chahiye (aur /health ko 503 dena chahiye). Agar hum yahan wait karte,
       toh F2 (shopdb band) mein shopapp start hi nahi hota aur incident
       "container crashed" dikhta, "database down" nahi — galat diagnosis.
    """
    app.state.pool = AsyncConnectionPool(
        DB_URL,
        min_size=POOL_MIN,
        max_size=POOL_MAX,
        open=False,
        timeout=HEALTH_TIMEOUT_S,  # pool se connection maangne ka max wait
        # ─────────────────────────────────────────────────────────────────────
        # 🔥 Ye ek line poore project ki measurement ko bachati hai.
        #
        # Problem: pool idle connections ko cache karta hai. Jab OnCall shopdb
        # ko restart karta hai (ya chaos usko todta hai), pool ke paas jo
        # connection pada hai wo MAR chuka hota hai — lekin pool ko nahi pata.
        # Agla `/health` probe usi laash ko utha leta hai aur fail ho jaata hai.
        #
        # Ab socho iska asar: probe #1 fail hua kyunki connection stale tha, na
        # ki kyunki fix kaam nahi kiya. `Verdict.disagreed` ka formula hai
        # `naive_recovered and not sustained_recovered` — ek jhootha fail
        # `naive_recovered` ko False kar deta hai, aur `disagreed` chup-chaap
        # False ho jaata. Yaani project ka headline result GAYAB ho jaata, aur
        # error message kahin nahi dikhta. Ye har fault ko chhoota hai, sirf F4
        # ko nahi — kyunki har fault ke baad ek restart hota hai.
        #
        # `check` ek callback hai jo pool connection dene se PEHLE chalata hai.
        # `check_connection` andar `conn.execute("")` karta hai — ek khaali
        # query, sirf protocol round-trip, `SELECT 1` se bhi sasta. Fail hua toh
        # pool us connection ko phenk ke naya banata hai (deadline tak retry).
        # ✅ psycopg source se verify kiya: `check: AsyncConnectionCB | None =
        #    None` (default = koi check nahi), aur `check_connection` ka
        #    docstring khud kehta hai "available for client usage, for instance
        #    as check callback when a pool is created" (2026-08-22).
        #
        # ⚠️ Iski keemat: har checkout pe ek extra round-trip (~0.2 ms local
        #    Docker network pe). 20 rps pe wo 4 ms/second hai. Sahi trade hai —
        #    ek jhoothi measurement ki keemat isse hazaar guna zyada hai.
        # ─────────────────────────────────────────────────────────────────────
        check=AsyncConnectionPool.check_connection,
    )
    await app.state.pool.open(wait=False)
    app.state.redis = aioredis.from_url(REDIS_URL, socket_timeout=HEALTH_TIMEOUT_S)
    try:
        yield
    finally:
        # 🔴 `finally` zaruri hai. Iske bina container SIGTERM pe connections
        #    chhod deta hai aur Postgres unhe ~30 second tak zinda maanta hai.
        #    Restart-heavy project mein wo leaked connections khud F4 bana
        #    dete hain — ek bug jo asli bug jaisa dikhta hai.
        await app.state.pool.close()
        await app.state.redis.aclose()


app = FastAPI(title="shopapp", lifespan=lifespan)


def _check_disk() -> str:
    """Disk check — ek ASLI write, plus free bytes evidence ke liye.

    🔴 Sirf free-space dekhna kaafi NAHI hai. Agar tmpfs mount hi ghayab ho,
       toh `shutil.disk_usage("/tmp/shopapp-cache")` parent filesystem (Docker
       VM ka `/`) ka jawaab deta hai — jahan gigabytes free hain. Check pass ho
       jaata aur F2 invisible reh jaata. Ek asli write DONO pakadta hai: bhara
       hua tmpfs (ENOSPC) aur ghayab/read-only mount.

    ⚠️ Ye blocking I/O ek async endpoint mein hai. Jaan-boojh kar: tmpfs
       RAM-backed hai, 2-byte write microseconds leta hai, aur BHARE hue tmpfs
       pe wo turant ENOSPC deta hai — latakta nahi. `run_in_executor` ka
       overhead khud write se zyada hota.
    """
    probe = CACHE_DIR / _PROBE_NAME
    try:
        # 🔴 mode `"w"`, `"x"` nahi. `"x"` (exclusive create) tab fail karta hai
        #    jab file pehle se maujood ho — aur pichhle probe ki file wahin padi
        #    hogi. Wo ek false alarm hota, disk full na hone pe bhi.
        with probe.open("w") as fh:
            fh.write("ok")
            # 🔴 flush + fsync ke bina Python ka userspace buffer kernel tak
            #    pahunchta hi nahi, aur ENOSPC chup-chaap `close()` pe aata
            #    (jahan koi use nahi pakad raha). Hum error YAHAN chahte hain.
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        # errno 28 = ENOSPC ("No space left on device") → yahi F2 hai.
        # errno 13 = EACCES → tmpfs ka mode galat hai. App uid 10001 pe chalta
        #            hai (Dockerfile ka `USER appuser`), toh compose mein
        #            `mode: 1777` chahiye. Ye CONFIG bug hai, fault nahi —
        #            isliye errno body mein jaata hai, taaki ek insaan farq
        #            kar sake aur compose file pe jaaye.
        return f"FAIL {type(exc).__name__}[errno={exc.errno}]: {str(exc)[:150]}"

    # Free bytes sirf EVIDENCE hai — LLM #1 ke diagnosis prompt ke liye. Verdict
    # write se banta hai, is number se nahi. Isliye ye fail ho toh bhi "ok".
    try:
        free = shutil.disk_usage(CACHE_DIR).free
        return f"ok (write ok, {free} bytes free)"
    except OSError:
        return "ok (write ok, free bytes unknown)"


@app.get("/health")
async def health() -> dict[str, Any]:
    """🔥 Wo endpoint jispe poora project khada hai.

    Teen cheezein check karta hai, aur teeno mein se koi bhi fail ho toh 503:

        db    → ek asli connection + `SELECT count(*) FROM orders`  (F3, F4)
        cache → Redis `PING`                                        (F5)
        disk  → CACHE_DIR mein ek asli 2-byte write                 (F2)

    (F1 = container hi mar jaata hai, toh endpoint jawaab hi nahi deta — uske
    liye koi check likhne ki zarurat nahi, connection refused hi signal hai.)

    503 ("Service Unavailable") deliberately chuna hai, 500 nahi — 500 "mera
    code toota" hai, 503 "meri dependency toot gayi" hai. Oracle dono ko
    unhealthy maanta hai, lekin logs padhne wale insaan ke liye farq hai.
    """
    started = time.perf_counter()
    checks: dict[str, str] = {}

    # ── 1. Database: ek asli connection + ek asli query ──────────────────────
    # `SELECT 1` nahi, `SELECT count(*) FROM orders` — kyun: `SELECT 1` ko
    # Postgres bina table chhue jawab de deta hai. Agar F3 (table lock) chal
    # raha hai, `SELECT 1` pass ho jaayega aur health jhooth bolega. Ek asli
    # table ko chhoona zaruri hai.
    try:
        async with app.state.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT count(*) FROM orders")
                row = await cur.fetchone()
        checks["db"] = f"ok ({row[0]} orders)"
    except Exception as exc:  # noqa: BLE001 — health check ko sab kuch pakadna hai
        # 🔴 Exception ka type NAHI chhupa rahe. `TooManyConnections` aur
        #    `OperationalError: timeout` do alag faults hain (F4 vs F2) aur
        #    LLM #1 ko diagnosis ke liye ye string milti hai. Isliye class ka
        #    naam aur message dono jaate hain.
        checks["db"] = f"FAIL {type(exc).__name__}: {str(exc)[:200]}"

    # ── 2. Cache ─────────────────────────────────────────────────────────────
    try:
        pong = await app.state.redis.ping()
        checks["cache"] = "ok" if pong else "FAIL no pong"
    except Exception as exc:  # noqa: BLE001
        checks["cache"] = f"FAIL {type(exc).__name__}: {str(exc)[:200]}"

    # ── 3. Disk ──────────────────────────────────────────────────────────────
    # 🔴 Ye check pehle MAUJOOD NAHI THA, aur uske bina F2 (disk full) ek aisa
    #    fault tha jo inject hota tha lekin detect nahi hota tha. Chaos disk
    #    bhar deta, /health 200 deta, incident khulta hi nahi. Ek fault jo
    #    detect nahi hota, wo measure bhi nahi hota — yaani F2 ka poora row
    #    METRICS.md mein khaali reh jaata.
    checks["disk"] = _check_disk()

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    healthy = all(v.startswith("ok") for v in checks.values())

    body = {"status": "ok" if healthy else "unhealthy", "checks": checks, "took_ms": elapsed_ms}
    if not healthy:
        # HTTPException ke saath status_code 503 — FastAPI ise JSON body ke
        # saath return karta hai, toh oracle ko body bhi milti hai debugging ke
        # liye, sirf status code nahi.
        raise HTTPException(status_code=503, detail=body)
    return body


@app.get("/orders")
async def list_orders(limit: int = 20) -> dict[str, Any]:
    """Ek asli business endpoint, taaki service ek khaali shell na lage.

    🔒 `limit` ko clamp kiya gaya hai. `?limit=999999999` ek chhota DoS hai —
    aur ye endpoint load generator ke neeche chalta hai, toh ek unbounded
    query poore measurement ko kharab kar deti.
    """
    limit = max(1, min(limit, 100))
    async with app.state.pool.connection() as conn:
        async with conn.cursor() as cur:
            # 🔴 F4 ka load-math isi line pe khada hai (upar ORDER_SLEEP_S ka
            #    comment). Connection HOLD hoti rehti hai sleep ke dauraan —
            #    wahi concurrency-need paida karta hai. Bound param, koi
            #    f-string nahi.
            if ORDER_SLEEP_S > 0:
                await cur.execute("SELECT pg_sleep(%s)", (ORDER_SLEEP_S,))
            # 🔒 Parameterised query. `f"... LIMIT {limit}"` kabhi nahi —
            #    chahe limit already int ho. Aadat hi aisi rakhni hai, warna
            #    ek din wo value user string hogi.
            await cur.execute(
                "SELECT id, customer, amount, status FROM orders ORDER BY id DESC LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()
    return {
        "count": len(rows),
        "orders": [{"id": r[0], "customer": r[1], "amount": float(r[2]), "status": r[3]} for r in rows],
    }


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "shopapp", "health": "/health", "orders": "/orders"}


# ─────────────────────────────────────────────────────────────────────────────
# 🔴 `import psycopg` ka note (upar `# noqa: F401` isi liye hai).
#
# Is file mein `psycopg.` kahin call nahi hota — sirf `AsyncConnectionPool`
# use hota hai, jo `psycopg_pool` se aata hai. Toh import kyun?
#
# Kyunki `psycopg_pool` ek pool hai, driver nahi. Wo andar `psycopg` ko import
# karta hai. Import ko yahan EXPLICIT rakhne ka ek hi faayda hai, aur wo
# faayda debugging ka hai: agar `psycopg[binary]` wheel container mein theek se
# install nahi hui, toh error is file ki line 1 pe milta hai — "ModuleNotFound:
# psycopg" — na ki 40 line neeche pool banane ke waqt ek uljhe hue
# `ImportError` ke andar. Ek dependency jispe hum khade hain, wo dikhni chahiye.
#
# Alternative: import hata do aur `psycopg_pool` pe bharosa karo. Wo kaam
# karta hai, aur agar tum is do-line ke tradeoff ko defend nahi karna chahte
# toh wo bhi theek hai — code identically chalta hai.
# ─────────────────────────────────────────────────────────────────────────────
