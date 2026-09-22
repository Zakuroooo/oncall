"""Schema verify + apply — ek schema file, do runner.

─────────────────────────────────────────────────────────────────────────────
🔴 YE FILE KYUN EXIST KARTI HAI (ek asli bug, jo iske bina hota hai)
─────────────────────────────────────────────────────────────────────────────
`stack/oncalldb/init.sql` Docker ke `/docker-entrypoint-initdb.d/` mein hai.
Postgres image use **sirf tab** chalati hai jab data directory KHAALI ho.

Matlab ye sequence bilkul possible hai, aur chup-chaap hoti hai:

    1. `make up` chalao          → schema ban gaya (khaali volume)
    2. init.sql mein table joda  → file badal gayi
    3. `make down && make up`    → volume purana hai → Docker init.sql SKIP karta
    4. app boot ho gaya          → koi error nahi, sab hara
    5. pehla incident            → `INSERT INTO verdicts` → relation does not exist

🔴 **Failure incident ke BEECH aati hai, boot pe nahi.** Yaani wo tab aati hai
   jab ek asli fault chal raha hai — aur uska error us fault jaisa dikhta hai.
   Ye `.env` ke silently-ignored key wali hi bimari hai (`ONCALL_GROQ_API_KEY`).

`verify()` isi ko boot pe zor se fail karta hai: "table `verdicts` nahi hai,
`make nuke && make up` chalao". 4 second mein, 40 minute ki debugging nahi.

─────────────────────────────────────────────────────────────────────────────
🔥 EK schema file, DO runner — aur `migrations/` folder DELETE kyun hua (D50)
─────────────────────────────────────────────────────────────────────────────
Purana plan (aur 10 doc references) kehte the: `migrations/001_init.sql`,
`002_...` — versioned files, `migrate.py` unhe order mein chalata hai.

Wo plan chhoda, kyunki usme **do file ban jaati thin**: ek jo Docker first-boot
pe chalata hai (`init.sql`), doosri jo `migrate.py` chalata hai
(`migrations/*.sql`) — aur donon ko same rakhne wala **kuch bhi nahi** tha. Do
copy ya ek symlink; symlink ko Docker ka COPY follow karega ⚠️ ye bhi pakka
nahi. Ye drift factory hai.

Design B: **ek hi file, `stack/oncalldb/init.sql`**. Docker use first boot pe
chalata hai, `migrate.py` use dobara chala sakta hai. "Kya wo drift hui?" ek
aisa sawaal hai jo poocha hi nahi ja sakta — ek hi file hai.

✅ Ye safe isliye hai kyunki wo file **provably re-runnable** hai (2026-08-22):

    grep "^CREATE\\|^ALTER\\|^DROP\\|^INSERT" stack/oncalldb/init.sql \\
        | grep -v "IF NOT EXISTS"        # → khaali (exit 1)

Saare 16 DDL statements `IF NOT EXISTS` ke saath hain, aur aakhri `DO $$` block
sirf tables ginta hai (read-only). Isliye "dobara chalana safe hai" ek
**check-able property** hai, ek ummeed nahi. `tests/test_schema.py` isi ko
har run pe verify karta hai.

─────────────────────────────────────────────────────────────────────────────
verify() vs apply() — default READ-ONLY hai (CLAUDE.md §3)
─────────────────────────────────────────────────────────────────────────────
| | Kya karta hai | Privileges | Kab chalta hai |
|---|---|---|---|
| `verify()` | 8 tables + `vector` extension maujood hain? | sirf CONNECT | **har startup pe** |
| `apply()`  | init.sql dobara chalata hai | CREATE | sirf `--write` pe |

Wajah: startup pe schema **banana** ek chupa hua write hai. Do process ek saath
boot ho (API + worker) toh dono CREATE maarte. `verify()` padhta hai, batata hai,
aur insaan decide karta hai.

─────────────────────────────────────────────────────────────────────────────
Related
─────────────────────────────────────────────────────────────────────────────
| Cheez | Kahan |
|---|---|
| Asli schema (381 line, 7 table) | `stack/oncalldb/init.sql` |
| Ye decision | `docs/DECISIONS.md` D50 |
| Text-only checks (bina DB) | `tests/test_schema.py` |
| Connection pool | `oncall/db/pool.py` |
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

# ═════════════════════════════════════════════════════════════════════════════
# 0. Schema file ka rasta — cwd se NAHI
# ═════════════════════════════════════════════════════════════════════════════
# `Path("stack/oncalldb/init.sql")` likhna galat hota: wo tab tootta hai jab
# koi `cd oncall && python -m db.migrate` chalata hai. `__file__` se chalna
# cwd-independent hai.
#
# parents[0] = oncall/db, parents[1] = oncall, parents[2] = repo root.
# 🔴 Repo ka naam yahan nahi likha — positional hai. Isliye jab `SETU/` ka naam
#    `oncall/` hoga, ye line badalni nahi padegi.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 🔴 Poore project mein schema ka EK maalik. Do jagah nahi.
SCHEMA_PATH = REPO_ROOT / "stack" / "oncalldb" / "init.sql"

#: `stack/oncalldb/init.sql` ke 8 tables — us file se haath se padhe gaye
#: (✅ 2026-08-22 pe 7 the; 2026-08-24 pe `retrievals` juda — N4 metric ki
#: query us table ko maangti thi aur wo exist hi nahi karti thi). 🔴 Ye list
#: purani hui toh `tests/test_schema.py` fail karega, chup nahi rahega.
EXPECTED_TABLES: tuple[str, ...] = (
    "incidents",
    "events",
    "llm_calls",
    "approvals",
    "verdicts",
    "retrievals",
    "runbooks",
    "jobs",
)

#: pgvector — `runbooks.embedding vector(384)` iske bina exist nahi kar sakta.
EXPECTED_EXTENSION = "vector"


# ═════════════════════════════════════════════════════════════════════════════
# 1. Result — plain data, print nahi
# ═════════════════════════════════════════════════════════════════════════════
# `verify()` seedha `print()` kar sakti thi, par phir usko test karne ke liye
# stdout capture karna padta. Data lautao; print CLI ka kaam hai. Wahi pattern
# `chaos/inject.py` ke `InjectResult` ka hai.


@dataclass(frozen=True)
class SchemaState:
    """Schema ki tasveer — ek boolean se zyada, kyunki "kya missing hai" chahiye."""

    reachable: bool
    """DB se connection bani? `False` ka matlab schema ke baare mein kuch nahi pata."""

    missing_tables: tuple[str, ...] = ()
    has_extension: bool = False
    error: str = ""
    present_tables: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        """Sab theek? 🔴 `reachable` bhi chahiye — warna ek down DB
        "0 missing tables" ke saath pass ho jaati (khaali list = sab mila)."""
        return self.reachable and not self.missing_tables and self.has_extension

    def summary(self) -> str:
        """Ek line, insaan ke liye."""
        if not self.reachable:
            return f"❌ oncall-db tak pahunch nahi: {self.error}"
        if self.ok:
            return (
                f"✅ schema poora — {len(self.present_tables)}/"
                f"{len(EXPECTED_TABLES)} tables + {EXPECTED_EXTENSION}"
            )
        parts = []
        if self.missing_tables:
            parts.append(f"missing tables: {', '.join(self.missing_tables)}")
        if not self.has_extension:
            parts.append(f"missing extension: {EXPECTED_EXTENSION}")
        return "❌ schema adhoora — " + "; ".join(parts)


# ═════════════════════════════════════════════════════════════════════════════
# 2. verify() — READ-ONLY. Har startup pe yahi chalti hai.
# ═════════════════════════════════════════════════════════════════════════════
def verify() -> SchemaState:
    """8 tables + `vector` extension maujood hain? Kuch likhta nahi.

    🔴 `information_schema.tables` isliye, `SELECT 1 FROM incidents LIMIT 1`
       nahi. Doosra tareeka table ko *chhoo* leta hai — aur agar kabhi row-level
       security ya permission ka issue hua, toh "table nahi hai" aur "table
       padh nahi sakte" ek jaisa dikhte. `information_schema` catalog padhta
       hai, data nahi.

    🔴 `table_schema = 'public'` filter zaroori hai. Bina uske, `pg_catalog` ke
       apne 60+ tables bhi count mein aa jaate — aur agar galti se koi
       `pg_catalog.jobs` jaisa naam mil gaya, verify jhooth bolti.
    """
    # Import yahan (function ke andar), module ke top pe nahi — kyunki
    # `tests/test_schema.py` is file ko bina psycopg import karta hai. Top-level
    # import hota toh pehli line pe hi ModuleNotFoundError.
    from oncall.db.pool import get_conn

    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                  FROM information_schema.tables
                 WHERE table_schema = 'public'
                   AND table_name = ANY(%s)
                """,
                (list(EXPECTED_TABLES),),
            )
            present = tuple(sorted(r["table_name"] for r in cur.fetchall()))

            cur.execute(
                "SELECT 1 AS found FROM pg_extension WHERE extname = %s",
                (EXPECTED_EXTENSION,),
            )
            has_ext = cur.fetchone() is not None
    except Exception as exc:  # noqa: BLE001 - startup check ko sab pakadna hai
        return SchemaState(reachable=False, error=f"{type(exc).__name__}: {exc}")

    missing = tuple(t for t in EXPECTED_TABLES if t not in present)
    return SchemaState(
        reachable=True,
        missing_tables=missing,
        has_extension=has_ext,
        present_tables=present,
    )


# ═════════════════════════════════════════════════════════════════════════════
# 3. apply() — sirf `--write` pe. Poori file ek hi execute mein.
# ═════════════════════════════════════════════════════════════════════════════
def _line_of_offset(text: str, offset: int) -> int:
    """Character offset (1-based, jo Postgres deta hai) → line number.

    Ye 3 line ka helper ek asli faayda deta hai: error message mein
    `init.sql:214` likha ho toh editor mein seedha khul jaata hai. Bina iske
    message hota "syntax error at character 8231" — jo kisi kaam ka nahi.
    """
    return text.count("\n", 0, max(offset - 1, 0)) + 1


def apply() -> None:
    """`stack/oncalldb/init.sql` dobara chalao. Idempotent hone pe bharosa.

    🔴 Poori file EK `cur.execute()` mein jaati hai — statements mein todi
       nahi jaati. Wajah: file ke aakhir mein ek `DO $$ ... $$` block hai
       jiske ANDAR semicolons hain. `;` pe split karne wala koi bhi splitter
       usko beech se kaat dega. Sahi splitter likhne ke liye dollar-quoting
       samajhni padti (~30 line, aur uska apna bug surface).

       Alternative jo choose nahi kiya: wo splitter. Jo mila uske badle: Postgres
       apne error mein `statement_position` deta hai, aur upar wala helper usko
       line number bana deta hai. Yaani "kaun statement fail hua" ka jawaab
       parser ke bina mil jaata hai.
    """
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(
            f"Schema file nahi mili: {SCHEMA_PATH}\n"
            "Ye repo root se relative hai; agar folder move hua hai toh "
            "`oncall/db/migrate.py` ka REPO_ROOT dekho."
        )

    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    from oncall.db.pool import get_conn

    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(sql)
    except Exception as exc:  # noqa: BLE001 - message banane ke liye
        # `diag` sirf psycopg ke DB errors pe hota hai; baaki exceptions pe nahi.
        pos = getattr(getattr(exc, "diag", None), "statement_position", None)
        where = ""
        if pos:
            where = f" (init.sql:{_line_of_offset(sql, int(pos))})"
        raise RuntimeError(
            f"Schema apply fail hua{where}: {type(exc).__name__}: {exc}\n"
            "🔴 Agar ye 'already exists' hai, toh koi DDL `IF NOT EXISTS` ke "
            "bina hai — `make lint` usko pakadta hai.\n"
            "Sabse saaf raasta: `make nuke && make up` (volume delete, "
            "phir se first boot)."
        ) from exc


# ═════════════════════════════════════════════════════════════════════════════
# 4. CLI — `python -m oncall.db.migrate` (default: kuch likhta nahi)
# ═════════════════════════════════════════════════════════════════════════════
_USAGE = """\
python -m oncall.db.migrate [--write]

  (koi flag nahi)  verify — 8 tables + `vector` extension check karta hai,
                   kuch likhta nahi. Exit 0 = poora, 1 = adhoora/pahunch nahi.
  --write          apply — stack/oncalldb/init.sql dobara chalata hai.
"""


def main(argv: list[str] | None = None) -> int:
    """Exit code lautata hai, `sys.exit()` khud nahi karta.

    🔴 Wajah: `main()` ko test se call kar sakein. `SystemExit` raise karne wala
       function test mein `pytest.raises(SystemExit)` maangta hai, aur uska
       message string bhi exit *code* ki jagah aa jaata hai — do jhooth ek saath.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if "--help" in args or "-h" in args:
        print(_USAGE)
        return 0

    write = "--write" in args
    unknown = [a for a in args if a not in {"--write"}]
    if unknown:
        print(f"❌ pata nahi ye kya hai: {' '.join(unknown)}\n\n{_USAGE}")
        return 2

    if not write:
        state = verify()
        print(state.summary())
        if not state.ok:
            print("\nTheek karne ka raasta: `make nuke && make up`")
            print("(ya soch-samajh kar: `python -m oncall.db.migrate --write`)")
        return 0 if state.ok else 1

    # --write path
    print(f"apply: {SCHEMA_PATH}")
    apply()
    state = verify()
    print(state.summary())
    return 0 if state.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
