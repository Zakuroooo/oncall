"""Schema guardrails — `stack/oncalldb/init.sql` ko TEXT ki tarah padhkar.

────────────────────────────────────────────────────────────────────────────
🔴 YE TEST BINA DATABASE KE CHALTA HAI — aur yahi iska poora point hai
────────────────────────────────────────────────────────────────────────────
Design B (D50) kehta hai: **ek schema file, do runner** — Docker first boot pe,
aur `migrate.py --write` uske baad. Wo tabhi safe hai jab file dobara chalane
pe fail na kare, yaani har DDL `IF NOT EXISTS` ke saath ho.

Ab sawaal: is property ko kaun pakadta rahega? Aaj wo sach hai. Kal koi ek
`CREATE INDEX` bina `IF NOT EXISTS` jod dega, aur kuch **nahi** hoga — kyunki
pehli baar (khaali volume pe) sab kaam karta hai. Bug 3 din baad `make down &&
make up` pe aayega, aur tab tak wajah bhool chuki hogi.

Ye file usi ko har run pe check karti hai. `psycopg` nahi chahiye, Docker nahi
chahiye, chalti hui DB nahi chahiye — sirf `pathlib` aur `re`. Isliye ye us
machine pe bhi chalti hai jahan `pip install` possible nahi.

────────────────────────────────────────────────────────────────────────────
Negative control — kyun 11 mein se TEEN check jaan-boojh kar GALAT input lete hain
────────────────────────────────────────────────────────────────────────────
Ek text-scanning check ke do "pass" ek jaise dikhte hain:

    (a) file saaf hai            → 0 violations
    (b) mera regex toota hua hai → 0 violations

Dono "ok" print karte hain. Farq batane ka ek hi tareeka hai: checker ko ek
banaya hua GANDA input do aur zid karo ki wo fail kare. `test_negative_control_*`
exactly wahi karta hai (`docs/LEARNINGS.md` L23).

🔥 Aur ye sirf theory nahi hai — is file ke do negative control asli bug ke
   BAAD jode gaye (dono 2026-08-22):

   (1) `test_control_pool_does_not_shadow_POOL_MAX` pehle regex use karta tha,
       aur wo `oncall/db/pool.py` ke **docstring** mein quote kiye gaye F4 math
       (`POOL_MAX = 10 → ...`) ko violation samajh kar fail ho gaya.
   (2) `test_no_select_star_under_oncall` ne bilkul wahi galti dohrayi:
       `oncall/db/queries.py` ka docstring apne hi rule ko quote karta hai
       (*"`SELECT *` kahin nahi"*), aur regex us zikr pe laal ho gaya.

   Dono baar check **apni hi documentation** pe fail ho raha tha. Dono ka fix
   `ast` hai, jo assignment/string-literal aur docstring ka farq jaanta hai.
   Do baar ho jaane ke baad ye ek niyam hai, ittefaq nahi: *jo rule apni hi
   documentation mein quote hota hai, uska enforcement parser se hota hai,
   regex se nahi.* Un fixes ko pin karne ke liye
   `test_negative_control_pool_name_check_can_fail` aur
   `test_negative_control_star_select_check_can_fail` dono taraf se zid karte
   hain — asli code pakdo, docstring ka zikr chhodo.

Ye pytest ke bina chalta hai — wajah `tests/test_no_shell.py` ke aakhir mein
likhi hai: is machine pe pytest install nahi hai, aur ek check jo aaj chal
sakta hai usko install ke intezaar mein rokna ulta hai.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = REPO_ROOT / "stack" / "oncalldb" / "init.sql"
ONCALL = REPO_ROOT / "oncall"

#: Wo DDL keywords jo dobara chalne pe "already exists" de sakte hain.
_DDL_START = re.compile(r"^\s*(CREATE|ALTER|DROP|INSERT)\b", re.IGNORECASE)

#: `CREATE TABLE IF NOT EXISTS <name>` se naam nikaalne ke liye.
_CREATE_TABLE = re.compile(
    r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


# ═════════════════════════════════════════════════════════════════════════════
# 0. SQL ko lines mein todna — comments aur dollar-quoted blocks hata kar
# ═════════════════════════════════════════════════════════════════════════════
def _significant_lines(sql: str) -> list[tuple[int, str]]:
    """`(line_number, line)` — comments aur `$$ ... $$` blocks nikal kar.

    🔴 Dono skip zaroori hain, aur wajah ek hi hai: is repo mein SQL ke andar
       **jaan-boojh kar** samjhaane wale comments hain. Ek comment mein likha
       `-- CREATE INDEX ... (HNSW, D28 pe band)` ek asli DDL jaisa dikhta hai.
       Bina skip ke ye check hamesha fail karta — aur hamesha-fail hone wala
       check ignore kiya jaane lagta hai, jiske baad asli violation chhup jaata.

    🔴 `$$` block isliye ki uske ANDAR semicolon aur SQL hota hai, par wo ek
       hi statement ka hissa hai. Yahi wajah `migrate.apply()` ke poori file ek
       execute mein bhejne ki bhi hai — dono jagah ek hi baat.
    """
    out: list[tuple[int, str]] = []
    in_dollar = False
    for n, raw in enumerate(sql.splitlines(), start=1):
        line = raw.split("--", 1)[0]  # inline comment bhi hata do
        if "$$" in line:
            # Ek line mein do `$$` = block khula aur band (ek-line DO). Count
            # odd hone pe state palatta hai.
            if line.count("$$") % 2 == 1:
                in_dollar = not in_dollar
            continue
        if in_dollar or not line.strip():
            continue
        out.append((n, line))
    return out


def _non_idempotent(sql: str) -> list[str]:
    """Wo DDL lines jinke paas `IF NOT EXISTS` nahi hai. Khaali list = saaf."""
    bad: list[str] = []
    for n, line in _significant_lines(sql):
        if not _DDL_START.match(line):
            continue
        if re.search(r"IF\s+NOT\s+EXISTS", line, re.IGNORECASE):
            continue
        bad.append(f"init.sql:{n}: {line.strip()[:72]}")
    return bad


# ═════════════════════════════════════════════════════════════════════════════
# 1. Checks
# ═════════════════════════════════════════════════════════════════════════════
def test_schema_file_exists() -> None:
    """Ek file, ek maalik (D50). Path galat hone pe baaki sab checks jhooth."""
    assert SCHEMA.exists(), f"schema file nahi mili: {SCHEMA}"
    n = len(SCHEMA.read_text().splitlines())
    assert n > 200, f"init.sql sirf {n} line ka hai — 7 tables itne mein nahi aate"


def test_every_ddl_is_idempotent() -> None:
    """🔴 Design B ki poori buniyaad — file dobara chalane pe fail na kare."""
    bad = _non_idempotent(SCHEMA.read_text())
    assert not bad, (
        f"{len(bad)} DDL statement `IF NOT EXISTS` ke bina hai. "
        "Iska matlab `migrate.py --write` doosri baar 'already exists' pe "
        "marega, aur Design B (ek file, do runner) toot jaayega:\n  "
        + "\n  ".join(bad)
    )


def test_negative_control_idempotency_check_can_fail() -> None:
    """🔥 Checker ko GANDA input do aur zid karo ki wo fail kare.

    Bina is check ke, ek toota hua regex bhi "0 violations" deta — aur wo
    exactly waisa hi dikhta jaisa ek saaf file (`docs/LEARNINGS.md` L23).
    """
    planted = "\n".join(
        [
            "-- CREATE TABLE fake_comment (x int);   <- comment, ignore hona chahiye",
            "CREATE TABLE IF NOT EXISTS fine (x int);",
            "CREATE INDEX bad_no_guard ON fine (x);",  # 🔴 yahi pakadna hai
            "DO $$ BEGIN CREATE TABLE inside_block (y int); END $$;",
        ]
    )
    found = _non_idempotent(planted)
    assert len(found) == 1, (
        "negative control fail: exactly 1 violation milni chahiye thi "
        f"(bad_no_guard), mili {len(found)} → {found}. "
        "Matlab checker ka regex ya comment/$$ skip toota hua hai."
    )
    assert "bad_no_guard" in found[0], f"galat line pakdi: {found[0]}"


def test_expected_tables_match_schema_file() -> None:
    """`migrate.EXPECTED_TABLES` aur init.sql — DONO taraf se match.

    🔴 Do-tarfa isliye: sirf "list ka har naam file mein hai" check karna ek
       poori class miss karta hai — nayi table file mein add ho aur list mein
       nahi, toh `verify()` usko kabhi miss nahi batayega. Wo ek **silent skip**
       hai, aur silent skip fail hone se zyada khatarnaak hai.
    """
    from oncall.db.migrate import EXPECTED_TABLES

    lines = _significant_lines(SCHEMA.read_text())
    in_file = {m.group(1) for _, line in lines if (m := _CREATE_TABLE.match(line))}

    missing_in_file = sorted(set(EXPECTED_TABLES) - in_file)
    missing_in_list = sorted(in_file - set(EXPECTED_TABLES))
    assert not missing_in_file, (
        f"EXPECTED_TABLES mein hai par init.sql mein nahi: {missing_in_file} "
        "→ `verify()` ek aisi table maang raha hai jo banti hi nahi"
    )
    assert not missing_in_list, (
        f"init.sql banata hai par EXPECTED_TABLES mein nahi: {missing_in_list} "
        "→ `verify()` isko kabhi check nahi karega (silent skip)"
    )


def test_vector_extension_is_created() -> None:
    """`runbooks.embedding vector(384)` iske bina exist nahi kar sakta."""
    sql = SCHEMA.read_text()
    assert re.search(
        r"CREATE\s+EXTENSION\s+IF\s+NOT\s+EXISTS\s+vector", sql, re.IGNORECASE
    ), "`CREATE EXTENSION IF NOT EXISTS vector` init.sql mein nahi mila"


def test_migrate_owns_exactly_one_schema_file() -> None:
    """Design B pin: `migrate.py` init.sql pe point kare, `migrations/*.sql` pe nahi."""
    src = (ONCALL / "db" / "migrate.py").read_text()
    assert 'SCHEMA_PATH = REPO_ROOT / "stack" / "oncalldb" / "init.sql"' in src, (
        "migrate.py ka SCHEMA_PATH badal gaya — Design B (ek file, ek maalik) "
        "ke liye wo `stack/oncalldb/init.sql` hona chahiye"
    )
    assert 'glob("migrations' not in src and "glob('migrations" not in src, (
        "migrate.py `migrations/*.sql` glob kar raha hai — wahi purana design "
        "hai jo D50 mein reject hua (do file, kuch unhe same nahi rakhta)"
    )


def _python_files() -> list[Path]:
    return sorted(p for p in ONCALL.rglob("*.py") if "__pycache__" not in p.parts)


def _star_select_hits(src: str, filename: str = "<planted>") -> list[str]:
    """`SELECT *` wali **asli SQL strings** — docstring ka zikr chhod ke.

    🔴 Ye pehle regex tha (`SELECT\\s+\\*` per line, `#` ke baad ka hissa hata
       kar) aur wo 2026-08-22 ko **toot gaya**. `oncall/db/queries.py` ka module
       docstring apne hi rule ko quote karta hai —

           2. 🔴 **`SELECT *` kahin nahi.** ...

       — aur regex ne usko violation samajh liya. Yaani check us file pe laal
       hua jo rule ko **follow** kar rahi thi, sirf isliye ki wo rule ko
       *likhti* bhi hai.

    🔥 Ye **wahi galti hai jo is file mein pehle ho chuki hai**, ek function ke
       faasle pe: `_pool_max_assignments()` ka header bilkul yahi kahani sunata
       hai (`POOL_MAX` ka docstring math). Do baar ho jaane ke baad ye ek
       "oops" nahi, ek **pattern** hai, aur is project ka niyam ban gaya hai:
       *jo rule apni hi documentation mein quote hota hai, uska enforcement
       regex se nahi, parser se hota hai* (`docs/LEARNINGS.md`, `CLAUDE.md` §2).

    Rule jo ye lagata hai: **docstring ke alawa** koi bhi string literal
    `SELECT *` nahi rakh sakti. Docstring = `ast.Expr` jiska value ek str
    `Constant` ho, aur jo Module/ClassDef/FunctionDef ke body ka **pehla**
    statement ho. Baaki har string literal code hai — chaahe wo `execute()` ko
    jaaye, ya `_SQL_*` constant mein baithi ho, ya kisi list mein.

    ⚠️ Ek jaanbujh ke chhoda gaya gap: `"SELECT " + "*"` ya
       `f"SELECT {star}"` ye check nahi pakdega. Wo obfuscation hai, galti nahi
       — aur is file ka kaam galti pakadna hai. `execute()` ke pehle argument pe
       f-string ka ban alag se `tests/test_queries.py` mein AST se lagta hai.
    """
    tree = ast.parse(src, filename=filename)

    # Pehle saare docstring nodes ki pehchaan (id se, value se nahi — do
    # alag-alag docstring same text rakh sakti hain).
    doc_nodes: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None) or []
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                doc_nodes.add(id(first.value))

    pattern = re.compile(r"SELECT\s+\*", re.IGNORECASE)
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in doc_nodes:
            continue
        if pattern.search(node.value):
            hits.append(f"{filename}:{node.lineno}: {node.value.strip()[:64]}")
    return hits


def test_no_select_star_under_oncall() -> None:
    """🔴 `runbooks` mein `is_relevant` hai — ground truth. `SELECT *` usko leak karta.

    Ye ban poore `oncall/` pe hai, sirf runbooks queries pe nahi. Wajah: "kaunsi
    query runbooks ko chhooti hai" ek judgement call hai jo review mein miss ho
    jaata hai. "Koi `SELECT *` nahi" ek aisa rule hai jise machine check kar
    sakti hai, aur uska nuksaan zero hai — explicit column list hamesha behtar
    hai (column jodne pe purani query ka behaviour nahi badalta).
    """
    hits: list[str] = []
    for path in _python_files():
        hits += _star_select_hits(
            path.read_text(), filename=str(path.relative_to(REPO_ROOT))
        )
    assert not hits, (
        "`SELECT *` mila. `runbooks` mein `is_relevant` (ground truth) hai — "
        "explicit column list likho:\n  " + "\n  ".join(hits)
    )


def test_negative_control_star_select_check_can_fail() -> None:
    """🔥 Teesra negative control — kyunki abhi is checker ka regex bhi toota tha.

    Do taraf se zid karta hai, `POOL_MAX` wale control ki tarah:
      (a) asli SQL string pakdi jaaye (chaahe wo constant mein ho ya
          `execute()` ke andar) → warna check bekaar hai
      (b) docstring ka zikr NA pakda jaaye → warna check us file pe fail karta
          hai jo rule ko dastavez karti hai, aur phir kisi din rule ki
          documentation hata di jaati hai (galat fix)
    """
    planted = "\n".join(
        [
            '"""Module docstring jisme rule quote hai: `SELECT *` kabhi nahi."""',
            "# SELECT * <- comment, ignore hona chahiye",
            "",
            "def f():",
            '    """Function docstring: SELECT * ka zikr, code nahi."""',
            '    _SQL_BAD = "SELECT * FROM runbooks"   # 🔴 violation 1',
            '    cur.execute("select   *  from jobs")  # 🔴 violation 2',
            '    ok = "SELECT id, title FROM runbooks"',
            "    return _SQL_BAD, ok",
        ]
    )
    found = _star_select_hits(planted)
    assert len(found) == 2, (
        "negative control fail: exactly 2 violation milni chahiye thi (line 6 "
        f"aur 7), mili {len(found)} → {found}. Matlab AST scan ya docstring ka "
        "farq toota hua hai."
    )
    assert all("runbooks" in f or "jobs" in f for f in found), (
        f"galat lines pakdi: {found}"
    )


def _pool_max_assignments(src: str, filename: str = "<planted>") -> list[str]:
    """`POOL_MAX` / `POOL_MIN` ko **assign** karne wali asli lines.

    🔴 Ye pehle regex tha (`^\\s*POOL_(MAX|MIN)\\s*=`) aur wo GALAT tha — us
       galti ko yahan likhna zaroori hai kyunki wo do baar ho sakti hai.
       `oncall/db/pool.py` ke header mein F4 ka math **quote** kiya gaya hai:

           POOL_MIN = 1  → idle pe 1 connection  → ... → 200 ✅
           POOL_MAX = 10 → load pe 10 maangta    → ... → 500 🔴

       Wo do line ek **docstring** ke andar hain — samjhaane ke liye, define
       karne ke liye nahi. Regex ne dono ko violation bola. Yaani check ne
       apni hi documentation pe fail kiya, aur us haalat mein insaan check ko
       nikaal deta hai (ya docstring se sach hata deta hai) — dono nuksaan.

    ✅ `ast` sirf asli assignment dekhta hai; docstring uske liye ek string
       constant hai, statement nahi. Yahi wajah `tests/test_no_shell.py` ke
       AST-based hone ki bhi hai (`.pyc` aur comments pe grep jhooth bolta hai).
    """
    banned = {"POOL_MAX", "POOL_MIN"}
    hits: list[str] = []
    for node in ast.walk(ast.parse(src, filename=filename)):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Name) and t.id in banned:
                hits.append(f"{filename}:{t.lineno}: {t.id} = ...")
    return hits


def test_control_pool_does_not_shadow_POOL_MAX() -> None:
    """D50 ka naam-monopoly rule, machine-checkable.

    `POOL_MAX` victim stack ka constant hai aur F4 ka aadha mechanism. Control
    plane usi naam ka apna constant rakhta toh koi "pool bada kar do" sochkar
    galat file chhed sakta tha — aur project ka headline result chup-chaap
    badal jaata.
    """
    path = ONCALL / "db" / "pool.py"
    src = path.read_text()
    defs = _pool_max_assignments(src, filename=str(path.relative_to(REPO_ROOT)))
    assert not defs, (
        "oncall/db/pool.py `POOL_MAX`/`POOL_MIN` define kar raha hai. Wo naam "
        "sirf `stack/shopapp/app.py` ka hai (F4 ka mechanism). Yahan "
        f"`CONTROL_POOL_MAX` use karo:\n  " + "\n  ".join(defs)
    )
    assert "CONTROL_POOL_MAX" in src, "pool.py mein CONTROL_POOL_MAX hi nahi hai"


def test_negative_control_pool_name_check_can_fail() -> None:
    """🔥 Doosra negative control — kyunki abhi is checker ka regex TOOTA tha.

    Do taraf se zid karta hai:
      (a) asli assignment pakdi jaaye  → warna check bekaar hai
      (b) docstring/comment ka zikr NA pakda jaaye → warna check apni hi
          documentation pe fail karta hai, aur phir wo nikaal di jaati hai
    """
    planted = "\n".join(
        [
            '"""Header jisme F4 ka math quote hai:',
            "",
            "    POOL_MIN = 1  → idle pe 1",
            "    POOL_MAX = 10 → load pe 10",
            '"""',
            "# POOL_MAX = 99  <- comment, ignore hona chahiye",
            "CONTROL_POOL_MAX = 4",
            "POOL_MAX = 10   # 🔴 yahi ek asli violation hai",
        ]
    )
    found = _pool_max_assignments(planted)
    assert len(found) == 1, (
        "negative control fail: exactly 1 violation milni chahiye thi (line 8), "
        f"mili {len(found)} → {found}. Matlab AST scan ya docstring/comment ka "
        "farq toota hua hai."
    )
    assert found[0].endswith("POOL_MAX = ..."), f"galat line pakdi: {found[0]}"



def test_files_were_actually_scanned() -> None:
    """🔥 Sabse boring check, aur sabse zaroori.

    Agar `_python_files()` kabhi khaali list de (path badal gaya, rglob galat),
    toh `SELECT *` wala check **0 hits** ke saath pass ho jaayega — clean bolega
    aur bilkul galat hoga. Ek check jo kuch dekhti hi nahi, wo fail hone se
    zyada khatarnaak hai (`docs/CODEMAP.md` §7c).
    """
    files = _python_files()
    assert len(files) >= 15, (
        f"sirf {len(files)} python file mili {ONCALL} ke neeche — "
        "scan hi nahi chala. Baaki checks ka pass matlab-less hai."
    )


# ═════════════════════════════════════════════════════════════════════════════
# 2. Runner — pytest ke bina (is machine pe wo install nahi hai)
# ═════════════════════════════════════════════════════════════════════════════
# 🔴 Ek check jo `_checks` mein nahi hai, wo KABHI nahi chalti — aur usko koi
#    error nahi batata. Naya `def test_...` likhne pe usko yahan bhi jodna
#    padta hai. (Yahi `tests/test_no_shell.py` mein bhi likha hai.)
if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(REPO_ROOT))

    _checks = (
        test_schema_file_exists,
        test_files_were_actually_scanned,
        test_negative_control_idempotency_check_can_fail,
        test_every_ddl_is_idempotent,
        test_expected_tables_match_schema_file,
        test_vector_extension_is_created,
        test_migrate_owns_exactly_one_schema_file,
        test_no_select_star_under_oncall,
        test_negative_control_star_select_check_can_fail,
        test_negative_control_pool_name_check_can_fail,
        test_control_pool_does_not_shadow_POOL_MAX,
    )
    _failed = 0
    print(f"schema: {SCHEMA.relative_to(REPO_ROOT)}")
    print(f"scanning {len(_python_files())} python file(s) under {ONCALL.name}/\n")
    for _check in _checks:
        try:
            _check()
        except AssertionError as exc:
            _failed += 1
            print(f"FAIL  {_check.__name__}\n      {exc}\n")
        else:
            print(f"ok    {_check.__name__}")
    print()
    if _failed:
        raise SystemExit(f"{_failed}/{len(_checks)} check fail hue")
    print(f"saare {len(_checks)} schema check pass hue (bina DB, bina psycopg)")
