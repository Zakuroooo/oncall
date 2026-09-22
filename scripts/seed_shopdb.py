#!/usr/bin/env python3
"""
scripts/seed_shopdb.py — shopdb ke `orders` table mein synthetic data bharta hai.

─────────────────────────────────────────────────────────────────────────────
KYUN YE SCRIPT EXIST KARTA HAI
─────────────────────────────────────────────────────────────────────────────
`stack/shopdb/init.sql` sirf 3 smoke-test rows daalta hai. Wo container ke
pehle boot pe chalta hai aur uska kaam sirf itna hai: "/health aur /orders
turant meaningful jawab dein".

Asli volume yahan se aata hai. ⚠️ Target ~5,000 rows. Kyun 5,000 aur 50 nahi:

  * `/health` ka query `SELECT count(*) FROM orders` hai. 3 rows pe Postgres
    usko itni jaldi kar deta hai ki wo storage layer ko chhoo kar bhi nahi
    chhoota jaisa lagta hai. 5,000 rows ek asli sequential scan hai — chhota,
    lekin asli. F3 fault (table pe ACCESS EXCLUSIVE lock) ka asar tab hi
    saaf dikhta hai jab query ke paas karne ko kuch ho.
  * `/orders` ka `ORDER BY id DESC LIMIT 20` ek khaali table pe khaali array
    deta hai — demo mein wo "kaam kar raha hai?" ka jawab nahi deta.

⚠️ 5,000 ek ESTIMATE hai, measurement nahi. `make stats` aur `/health` ki
   latency Phase 1 mein isko check karegi. Agar 5,000 rows pe bhi count(*)
   sub-millisecond hai, ye number badhega — aur tab ye comment update hoga
   (CLAUDE.md §2: number badla toh date ke saath likho).

─────────────────────────────────────────────────────────────────────────────
🔴 DRY-RUN BY DEFAULT — CLAUDE.md §3 ka hard constraint
─────────────────────────────────────────────────────────────────────────────
Ye script bina `--write` ke database ko CHHOOTA BHI NAHI. Default mein wo
sirf batata hai ki kya karega, aur 5 sample rows dikhata hai.

Kyun ye rule exist karta hai: is repo mein do Postgres hain — shopdb (5432)
aur oncall-db (5433). Ek galat `--dsn` ka matlab hai ki 5,000 fake orders
control plane ke database mein chale jaayen. `--write` ek speed-bump hai jo
"maine bas dekhne ke liye chalaya tha" aur "maine likhwa diya" ke beech ek
DELIBERATE step daal deta hai.

─────────────────────────────────────────────────────────────────────────────
🔴 DETERMINISTIC — `--seed` se
─────────────────────────────────────────────────────────────────────────────
`Faker.seed(n)` aur `random.seed(n)` dono set hote hain. Matlab same seed pe
`customer`, `amount`, `status` BILKUL same aate hain.

Kyun ye maayne rakhta hai: agar kal `/health` ki latency badal jaaye, toh
sawaal "data badla ya code badla?" ka jawab dena chahiye. Ek fixed seed uska
jawab "code" bana deta hai.

⚠️ Ek imaandar exception: `created_at`. Wo deewar ki ghadi se anchor hota hai
   ("pichhle 90 din"), seed se nahi — toh do run ke timestamps alag honge.
   Ye jaan-boojh kar hai: is project ki koi query `created_at` pe filter ya
   order nahi karti (`/orders` `ORDER BY id DESC` karta hai), toh usko freeze
   karne se ek bhi measurement nahi badalta, aur ek hardcoded 2026 ka anchor
   agle saal bekaar lagta.

   Phir bhi `make_rows()` ka `now` ek PARAMETER hai, hidden `datetime.now()`
   call nahi — isse test ek fixed anchor de kar byte-identical output nikaal
   sakta hai. (Wo function ka docstring poori kahani batata hai; ye claim
   pehle galat likha tha aur ek test ne pakda.)

─────────────────────────────────────────────────────────────────────────────
DATA PROVENANCE (DATA.md A6)
─────────────────────────────────────────────────────────────────────────────
Ye data Faker se GENERATE hota hai — kahin se FETCH nahi hota. Toh:
  * koi asli customer nahi, koi PII nahi, koi licence nahi.
  * CLAUDE.md §3 ka "Pranay never collects data" yahan trivially satisfied hai.
  * CLAUDE.md §3 ka "every ingested record stores its source, licence, and
    fetch date" is table pe LAAGU NAHI hota — wo rule ingested corpus ke liye
    hai (`runbooks`), generated fixtures ke liye nahi. Wahan `source`,
    `licence`, `fetched_at` columns NOT NULL hain; yahan wo columns hain hi
    nahi, jaan-boojh kar. Ye farq is comment ke bina ek reviewer ko
    inconsistency lagega — isliye ye comment hai.

Usage:
    python scripts/seed_shopdb.py                    # dry run — kuch nahi likhta
    python scripts/seed_shopdb.py --write            # 5,000 rows daalta hai
    python scripts/seed_shopdb.py --write --rows 200 # chhota run
    python scripts/seed_shopdb.py --write --truncate # pehle table khaali karta hai
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

# ═════════════════════════════════════════════════════════════════════════════
# 🔴 OPTIONAL DEPENDENCIES — aur import-time `sys.exit()` KYUN hata (D51)
# ═════════════════════════════════════════════════════════════════════════════
# Pehle in do import ke around ek `try/except ImportError: sys.exit(...)` tha.
# Ek script ke liye wo theek dikhta hai. Lekin usme do asli problem thin:
#
#   1. `sys.exit()` `SystemExit` raise karta hai, aur 🔴 `SystemExit` **`Exception`
#      se inherit NAHI karta**. Matlab jo bhi is file ko import kare, uska
#      `except ImportError` ya `except Exception` isko pakad hi nahi sakta —
#      poora process mar jaata hai. `tests/test_seed.py` exactly ye karta hai
#      (`importlib` se ise module ki tarah load karta hai), toh us file ke us
#      waqt ke 17 tests (aaj 18) is machine pe **collect hone se pehle** hi mar
#      jaate the. ✅ 2026-08-22 pe measure kiya: import karne pe
#      `SystemExit(code='❌ Dependency missing: psycopg...')` — ek bhi test chala nahi.
#
#   2. Dono dependencies EK hi `try` mein thin, par unke consumers alag hain:
#      `make_rows()`/`summarise()` ko sirf `faker` chahiye, `psycopg` sirf
#      `current_state()`/`write_rows()` ko. Ek saath baandhne ka matlab tha ki
#      pure-function tests bhi ek database driver ka intezaar karte the.
#
# Ab pattern wahi hai jo is repo mein pehle se do jagah hai — `oncall/db/pool.py`
# ka `_NoPsycopg` aur `chaos/faults.py` ka `_NoDockerSDK`: import fail hone par
# ek sentinel, aur ASLI istemaal pe ek saaf message. Friendly setup-hint ab
# `main()` deta hai (jahan insaan dekh raha hai), import time pe nahi.


class _MissingDep(Exception):
    """Placeholder aur asli error — dono ek hi class.

    🔴 Kaam sirf itna: neeche `except psycopg.OperationalError:` wali line
       COMPILE ho jaaye jab psycopg maujood na ho. `= Exception` likhna galat
       hota — wo *har* error ko connection-error maan leta (`pool.py` ka
       `_NoPsycopg` docstring wahi baat kehta hai).
    """


_SETUP_HINT = (
    "Ye script venv ke andar chalti hai. Setup:\n"
    "     make venv && make install\n"
    "     source .venv/bin/activate"
)


class _NoPsycopg:
    """psycopg ki jagah khada ek stand-in.

    Do kaam karta hai: attribute access (`psycopg.OperationalError`) chalti
    rehti hai, aur `connect()` maangne pe LOUDLY marta hai. Chup-chaap `None`
    return karna sabse bura hota — phir failure DB layer ke andar kahin aata.
    """

    OperationalError = _MissingDep
    Error = _MissingDep

    @staticmethod
    def connect(*_a: object, **_kw: object) -> None:
        raise _MissingDep(f"Dependency missing: psycopg\n   {_SETUP_HINT}")


try:
    import psycopg

    _HAS_PSYCOPG = True
except ModuleNotFoundError:  # pragma: no cover - install state pe depend karta hai
    psycopg = _NoPsycopg  # type: ignore[assignment]
    _HAS_PSYCOPG = False

# `faker` alag `try` mein hai kyunki uska consumer alag hai: sirf `make_rows()`.
# ✅ 2026-08-22 pe is separation ka asli payoff measure kiya:
#     python3 tests/test_seed.py   →  5 pass · 13 skip · 0 fail   (exit 0)
#   Yaani saare **18 checks collect aur run hote hain bina psycopg** — kyunki
#   koi bhi check psycopg ko chhoota nahi. Jo 13 skip hoti hain, unhe `faker`
#   chahiye (rows generate karne ke liye), psycopg nahi. Aur jo 5 chalti hain
#   wo isliye chalti hain ki `main()` mein flag validation dependency check se
#   PEHLE hai — ek CLI galti dependency install kiye bina bhi pakdi jaati hai.
#   🔴 Purana comment yahan "13 pure-function tests bina psycopg chal sakte
#      hain" kehta tha. Wo do tarah se galat tha: number 13 nahi 18 hai, aur
#      blocker psycopg nahi faker hai. Measure karne se pehle likha gaya tha.
try:
    from faker import Faker

    _HAS_FAKER = True
except ModuleNotFoundError:  # pragma: no cover - install state pe depend karta hai
    Faker = None  # type: ignore[assignment,misc]
    _HAS_FAKER = False

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_ROWS = 5_000
DEFAULT_SEED = 42
DAYS_BACK = 90

# 🔴 Ye 4 statuses `stack/shopdb/init.sql` ke CHECK constraint se EXACTLY match
#    karte hain. Ek paanchwa status yahan add karne se database `INSERT` reject
#    karega — aur wahi sahi behaviour hai (constraint apna kaam kar raha hai).
#    Weights realistic hain: zyada orders paid/shipped hote hain, kam cancelled.
#
#    ⚠️ Ye weights ek guess hain, kisi asli dataset se nahi aaye. Wo theek hai:
#    inka kaam sirf `orders_status_idx` ko ek non-uniform distribution dena hai
#    (ek index jahan har value equally common ho, wo selectivity ke hisaab se
#    bekaar hota hai). Koi measurement in numbers pe khada nahi hai.
STATUS_WEIGHTS: dict[str, int] = {
    "pending": 15,
    "paid": 45,
    "shipped": 35,
    "cancelled": 5,
}

# 🔴 Amount ke liye lognormal, uniform nahi.
#
#    `random.uniform(1, 5000)` ka matlab hota ki ₹4,900 ka order utna hi common
#    hai jitna ₹50 ka. Asli e-commerce mein aisa nahi hota — chhote orders bahut
#    zyada hote hain, bade orders ek lambi patli tail banate hain. Lognormal
#    exactly wahi shape hai.
#
#    mu=6.2 → median ≈ e^6.2 ≈ ₹493. sigma=0.9 → tail ~₹2,200 tak (p95).
#    Kyun ye maayne rakhta hai: `numeric(10,2)` column ka `CHECK (amount >= 0)`
#    hai; ek realistic distribution us constraint ko asli test karti hai, aur
#    demo mein `/orders` ka output nakli nahi lagta.
AMOUNT_MU = 6.2
AMOUNT_SIGMA = 0.9
AMOUNT_MIN = Decimal("1.00")
AMOUNT_MAX = Decimal("99999.99")  # numeric(10,2) mein aaram se fit ho jaata hai

# 🔴 Default DSN loopback pe hai — `shopdb`, `localhost` nahi. Kyun:
#    ye script HOST pe chalti hai, container ke andar nahi. Container ke andar
#    `shopdb` DNS naam kaam karta hai; host pe wo resolve hi nahi hota.
#    `127.0.0.1:5432` = docker-compose.yml ka published port.
DEFAULT_DSN = os.getenv("SHOPDB_DSN", "postgresql://shop:shop@127.0.0.1:5432/shop")


# ── Pure data generation — koi database yahan nahi ────────────────────────────


def make_rows(
    n: int,
    seed: int,
    now: datetime | None = None,
) -> list[tuple[str, Decimal, str, datetime]]:
    """
    n rows banata hai. 🔴 Ye function database ko CHHOOTA NAHI.

    Kyun ye alag function hai: isse dry-run bilkul wahi rows dikhata hai jo
    `--write` likhega — koi "preview mein kuch aur, asli mein kuch aur" ka
    farq nahi. Aur ye pure function hai, toh test ke liye ek live Postgres
    ki zarurat nahi (`tests/test_seed.py`, Phase 1).

    🔴 `now` ek PARAMETER hai, ek hidden `datetime.now()` call nahi. Ye ek asli
       bug ka fix hai jo maine likhne ke baad pakda:

       Pehle is function ke andar `now = datetime.now(timezone.utc)` tha. Docstring
       daawa karti thi "same seed = bilkul same rows". Wo daawa JHOOTA tha —
       `customer`, `amount`, `status` seed se aate the, lekin `created_at` deewar
       ki ghadi se. Do call, do alag timestamps, aur ek test jo determinism check
       karta hai wo fail ho jaata (aur hua bhi).

       Ye "hidden clock dependency" ek classic testability bug hai: ek function
       jo chupke se `now()` padhta hai wo pure nahi hota, aur usko exactly
       reproduce karna impossible ho jaata. Time ko argument bana dena usko
       wapas pure bana deta hai.

       Default (`None`) mein behaviour wahi hai jo pehle tha — script chalane
       wale ko kuch pass karne ki zarurat nahi. Sirf test ek fixed anchor deta
       hai aur byte-identical output paa jaata hai.
    """
    # 🔴 `faker` ke bina yahan ek *catchable* error uthta hai, `sys.exit()` nahi
    #    (D51). Wajah: caller ko farq karna chahiye "dependency nahi hai" aur
    #    "logic toot gaya" mein. `tests/test_seed.py` isi ko pakad kar 17 tests
    #    ko LOUDLY skip karta hai — crash nahi hota, aur chup-chaap pass bhi
    #    nahi hota.
    #
    #    Fallback naam generator jaan-boojh kar NAHI banaya: phir "same seed =
    #    same rows" ka daawa is baat pe depend karta ki faker install hai ya
    #    nahi — yaani ek environment-dependent determinism, jo determinism ka
    #    ulta hai.
    if not _HAS_FAKER:
        raise _MissingDep(f"Dependency missing: faker\n   {_SETUP_HINT}")

    # Do alag RNG seed karne padte hain kyunki Faker apna internal generator
    # rakhta hai. Sirf `random.seed()` karne se naam non-deterministic reh
    # jaate — ek aadha-deterministic script sabse confusing kism hoti hai.
    random.seed(seed)
    Faker.seed(seed)
    fake = Faker()

    statuses = list(STATUS_WEIGHTS.keys())
    weights = list(STATUS_WEIGHTS.values())

    # 🔴 `now` ek baar compute hota hai, loop ke andar nahi. Loop ke andar
    #    `datetime.now()` call karne se ek hi run ke andar timestamps drift
    #    karte — aur 5,000 iterations mein 5,000 syscalls bekaar jaate.
    if now is None:
        now = datetime.now(timezone.utc)
    window = timedelta(days=DAYS_BACK)

    rows: list[tuple[str, Decimal, str, datetime]] = []
    for _ in range(n):
        amount = Decimal(f"{random.lognormvariate(AMOUNT_MU, AMOUNT_SIGMA):.2f}")
        amount = max(AMOUNT_MIN, min(amount, AMOUNT_MAX))

        # created_at UNIFORM hai pichhle 90 din mein — koi recency bias nahi.
        # Imaandari se: ek recency-skewed distribution zyada realistic hoti,
        # lekin is project ka koi query `created_at` pe filter ya order nahi
        # karta (`/orders` `ORDER BY id DESC` karta hai). Toh wo extra
        # sophistication ek bhi measurement nahi badalti — aur bina wajah ka
        # code wo cheez hai jise interview mein defend nahi kiya ja sakta.
        created_at = now - timedelta(seconds=random.uniform(0, window.total_seconds()))

        rows.append(
            (
                fake.name(),
                amount,
                random.choices(statuses, weights=weights, k=1)[0],
                created_at,
            )
        )
    return rows


def summarise(rows: list[tuple[str, Decimal, str, datetime]]) -> str:
    """Generated rows ka ek chhota stats block — dry-run mein yahi dikhta hai."""
    if not rows:
        return "  (koi row nahi)"

    counts: dict[str, int] = {}
    for _, _, status, _ in rows:
        counts[status] = counts.get(status, 0) + 1

    amounts = sorted(amt for _, amt, _, _ in rows)
    total = sum(amounts)
    lines = [
        f"  rows          : {len(rows):,}",
        f"  amount min/med/max : {amounts[0]} / {amounts[len(amounts) // 2]} / {amounts[-1]}",
        f"  amount total  : {total:,.2f}",
        "  status breakdown :",
    ]
    for status in STATUS_WEIGHTS:
        c = counts.get(status, 0)
        lines.append(f"      {status:<10} {c:>6,}  ({c / len(rows) * 100:5.1f}%)")
    return "\n".join(lines)


# ── Database side ────────────────────────────────────────────────────────────


def current_state(dsn: str) -> str:
    """
    Database ka abhi ka haal padhta hai. SIRF PADHTA HAI — koi write nahi.

    Dry-run mein bhi ye chalta hai, kyunki "5,000 rows daalunga" ek adhoora
    statement hai jab tak ye pata na ho ki table mein pehle se kya hai.

    🔴 Ye function EXCEPTION nahi phenkta — ek insaan-padhne-layak line lautata
       hai, chahe DB down ho ya driver hi install na ho. Wajah: iska caller
       dry-run hai, aur dry-run ka kaam (rows generate karna) database se
       bilkul independent hai. Ek missing driver ko preview rokne dena us
       independence ko tod deta.
    """
    if not _HAS_PSYCOPG:
        return (
            "  ⚠️ psycopg install nahi hai — table ka haal padha nahi ja saka.\n"
            "     (dry-run phir bhi poora chalta hai; `--write` ke liye "
            "`make install` chahiye)"
        )
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                # `to_regclass` NULL deta hai agar table exist nahi karta —
                # exception throw karne ki jagah. Isse "database up hai lekin
                # init.sql nahi chali" ko "database hi down hai" se alag kar
                # sakte hain. Do bilkul alag problems, do alag fixes.
                cur.execute("SELECT to_regclass('public.orders') IS NOT NULL")
                exists = cur.fetchone()[0]
                if not exists:
                    return (
                        "  ⚠️ `orders` table maujood NAHI hai.\n"
                        "     Matlab init.sql nahi chali. Fix: make nuke && make up\n"
                        "     (init.sql sirf khaali data directory pe chalti hai)"
                    )
                cur.execute("SELECT count(*), coalesce(max(id), 0) FROM orders")
                n, max_id = cur.fetchone()
                return f"  abhi table mein : {n:,} rows (max id = {max_id})"
    except psycopg.OperationalError as exc:
        # Ye ek EXPECTED failure hai, bug nahi — shopdb down ho sakta hai.
        # Dry-run ko isse rukna NAHI chahiye: rows generate karna database se
        # bilkul independent hai, aur preview phir bhi kaam ka hai.
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        return f"  ⚠️ shopdb se connect nahi hua: {first_line}\n     (`make up` chala hai?)"


def write_rows(dsn: str, rows: list[tuple[str, Decimal, str, datetime]], truncate: bool) -> int:
    """
    Rows ko `orders` mein daalta hai. Sirf `--write` ke saath call hota hai.

    🔴 COPY, `INSERT` nahi. Ye is script ka sabse seekhne-layak hissa hai.

       `INSERT INTO orders VALUES (...)` 5,000 baar chalane ka matlab hai 5,000
       round-trips (ya psycopg ke `executemany` se ek pipelined batch). COPY ek
       hi statement hai jisme Postgres ek stream padhta hai — wo bulk load ke
       liye bana hi isliye hai. 5,000 rows pe farq ⚠️ chhota hoga (dono ek
       second se kam), lekin COPY hi wo tareeka hai jo Postgres walon ka
       standard hai, aur ek interview mein "kyun COPY" ka jawab
       "kyunki bulk load ka yahi tool hai" hona chahiye — na ki
       "kyunki mujhe INSERT nahi aata".

       Alternative jo reject hua: `executemany` ke saath 5,000 tuples. Kaam
       karta, ~15 lines mein wahi natija. Reject kiya kyunki COPY ka concept
       (server-side streaming bulk load) is project ke Postgres-heavy design
       mein baar-baar kaam aayega, aur ise ek baar samajh lena behtar hai.

    🔴 `id` column COPY ki list mein NAHI hai. `id` `bigserial` hai — Postgres
       khud sequence se bharta hai. Usko manually bharne se sequence peechhe
       reh jaata aur agla asli INSERT duplicate-key error deta. Ye ek classic
       "seed script ne production tod diya" bug hai.
    """
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            if truncate:
                # 🔴 `TRUNCATE ... RESTART IDENTITY` — sirf rows nahi, sequence
                #    bhi 1 pe wapas. Iske bina naya data id 5001 se shuru hota
                #    aur "5,000 rows lekin max id 10,000" ek confusing state
                #    banti. `CASCADE` nahi lagaya jaan-boojh kar: is table pe
                #    koi foreign key nahi hai, aur `CASCADE` ka matlab hota
                #    "jo bhi juda ho use bhi uda do" — wo ek seed script ko
                #    di jaane wali power nahi hai.
                cur.execute("TRUNCATE orders RESTART IDENTITY")

            with cur.copy("COPY orders (customer, amount, status, created_at) FROM STDIN") as cp:
                for row in rows:
                    cp.write_row(row)

            cur.execute("SELECT count(*) FROM orders")
            total = cur.fetchone()[0]
        # `with psycopg.connect(...)` block khatam hone pe COMMIT hota hai.
        # 🔴 psycopg 3 mein ye autocommit NAHI hai — connection context manager
        #    exit pe commit karta hai (exception pe rollback). Isliye yahan koi
        #    explicit `conn.commit()` nahi hai, aur uska hona ek redundant line
        #    hoti. Ye psycopg 2 se ek asli farq hai jo confuse karta hai.
    return total


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="shopdb ke orders table mein synthetic data bharta hai (dry-run by default).",
    )
    # 🔴 `--write` ka `action="store_true"` matlab default `False`. Ye flag hi
    #    CLAUDE.md §3 ka enforcement hai. Ek `--dry-run` flag (jise lagana
    #    padta) ulta hota aur galti se skip ho sakta tha.
    parser.add_argument("--write", action="store_true", help="asli mein database mein likho")
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS, help=f"kitni rows (default {DEFAULT_ROWS})")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"RNG seed (default {DEFAULT_SEED})")
    parser.add_argument("--dsn", default=DEFAULT_DSN, help="shopdb ka connection string")
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="likhne se PEHLE table khaali karo (destructive; --write ke saath hi chalta hai)",
    )
    args = parser.parse_args()

    if args.rows < 1:
        return _fail("--rows kam se kam 1 hona chahiye")
    if args.truncate and not args.write:
        # Ek chup-chaap ignore kiya gaya destructive flag sabse khatarnaak hai:
        # user sochta hai truncate ho gaya, hua nahi. Loudly fail karo.
        return _fail("--truncate ke liye --write bhi chahiye (dry-run kuch delete nahi karta)")

    # 🔴 Dependency check yahan hai — module import ke waqt nahi (D51). Do
    #    faayde jo purane `sys.exit()` ke saath nahi the:
    #      * `--help` aur `--rows 0` jaisi galtiyan bina venv ke bhi sahi jawab
    #        deti hain (✅ 2026-08-22 pe dono chala kar dekha).
    #      * message MODE ke hisaab se hai: `psycopg` sirf `--write` ke liye
    #        chahiye, toh dry-run usko maangta hi nahi.
    if not _HAS_FAKER:
        return _fail(f"Dependency missing: faker — rows generate nahi ho sakti\n   {_SETUP_HINT}")
    if args.write and not _HAS_PSYCOPG:
        return _fail(f"Dependency missing: psycopg — `--write` ke liye zaroori hai\n   {_SETUP_HINT}")

    # 🔴 DSN ko print karne se pehle password hata do. Ye script terminal pe
    #    chalti hai aur uska output screenshot/paste ho sakta hai.
    safe_dsn = args.dsn
    if "@" in safe_dsn and "://" in safe_dsn:
        scheme, rest = safe_dsn.split("://", 1)
        creds, host = rest.split("@", 1)
        user = creds.split(":", 1)[0]
        safe_dsn = f"{scheme}://{user}:***@{host}"

    print("─" * 70)
    print("OnCall — shopdb seed")
    print("─" * 70)
    print(f"  mode          : {'WRITE ✍️' if args.write else 'DRY RUN (kuch nahi likhega)'}")
    print(f"  dsn           : {safe_dsn}")
    print(f"  seed          : {args.seed}   (same seed = same rows)")
    print(f"  truncate      : {args.truncate}")
    print(current_state(args.dsn))
    print()

    rows = make_rows(args.rows, args.seed)
    print("  generate hua:")
    print(summarise(rows))
    print()
    print("  pehli 5 rows (yahi rows likhi jaayengi):")
    for customer, amount, status, created_at in rows[:5]:
        print(f"      {customer:<28} {amount:>10}  {status:<10} {created_at:%Y-%m-%d %H:%M}")
    print()

    if not args.write:
        print("  🔒 DRY RUN — database chhua bhi nahi gaya.")
        print("     Asli mein likhne ke liye: --write lagao.")
        print("─" * 70)
        return 0

    try:
        total = write_rows(args.dsn, rows, args.truncate)
    except psycopg.OperationalError as exc:
        return _fail(f"shopdb se connect nahi hua: {str(exc).strip().splitlines()[0]}\n   `make up` chala hai?")
    except psycopg.Error as exc:
        # 🔴 `psycopg.Error` ke andar constraint violations aate hain. Agar
        #    STATUS_WEIGHTS mein koi aisa status add ho jo init.sql ke CHECK
        #    mein nahi hai, error yahan aayega — aur wo message batata hai ki
        #    database ne apna kaam kiya, script ne galti ki.
        return _fail(f"database ne insert reject kiya: {type(exc).__name__}: {exc}")

    print(f"  ✅ likh diya. `orders` mein ab {total:,} rows hain.")
    print("     Verify: curl -s http://127.0.0.1:8001/health | python3 -m json.tool")
    print("─" * 70)
    return 0


def _fail(msg: str) -> int:
    print(f"❌ {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
