"""`oncall/db/queries.py` ke guardrails — bina database, bina psycopg.

────────────────────────────────────────────────────────────────────────────
Ye file kya rokti hai
────────────────────────────────────────────────────────────────────────────
`queries.py` mein teen claim likhe hain (uske module docstring mein), aur ek
comment `STATES`/`EVENT_KINDS` ke bare mein. Chaaron **is file ke bharose** pe
likhe hain. Agar ye file na ho, toh wo claim sirf ummid hain — aur `CLAUDE.md`
§4 kehta hai: ⚠️ assumption ko ✅ fact ki tarah pesh nahi karna.

  1. `cur.execute()` ka pehla argument hamesha literal ya `_SQL_*` naam hai
     → `test_execute_first_arg_is_literal_or_name`
  2. `STATES` / `TERMINAL_STATES` / `EVENT_KINDS` `init.sql` se match karte hain
     → `test_states_match_init_sql`, `test_event_kinds_match_init_sql`,
       `test_terminal_states_match_init_sql`
  3. `RUNBOOK_COLUMNS` mein ground truth nahi hai
     → `test_runbook_columns_hide_ground_truth`
  4. Kahin `conn.commit()` nahi hai (psycopg 3 ka `with` block commit karta hai)
     → `test_no_explicit_commit`

🔴 `SELECT *` ka ban is file mein **nahi** hai — wo `tests/test_schema.py` ka
   `test_no_select_star_under_oncall` hai, aur wo poore `oncall/` pe lagta hai.
   Ek rule do jagah check karna matlab ek din wo do jagah **alag** ho jaayega.

────────────────────────────────────────────────────────────────────────────
Kyun AST, aur kyun grep nahi — teesri baar
────────────────────────────────────────────────────────────────────────────
Mera pehla plan tha: `command grep -nE 'execute\\((f"|f.)' oncall/db/queries.py`.
Wo grep chal ke **fail** hua — `queries.py` ke docstring pe, jo bad pattern ko
example ki tarah quote karta hai. Us se pehle yahi galti `POOL_MAX` check pe
hui, aur uske turant baad `SELECT *` check pe.

Teen baar ke baad ye is project ka likha hua niyam hai: *jo rule apni hi
documentation mein quote hota hai, uska enforcement parser se hota hai.* `ast`
ke liye docstring ek `Constant` hai, `Call` nahi — wo confuse nahi hota.

────────────────────────────────────────────────────────────────────────────
`init.sql` ko TEXT ki tarah parse kyun
────────────────────────────────────────────────────────────────────────────
`STATES` ek **copy** hai (D50: `init.sql` owner hai). Copy ka khatra drift hai:
kal koi schema mein 11waan state jode aur `queries.py` 10 pe rah jaaye. Us
haalat mein `advance_state(to="NEW_STATE")` ek `ValueError` deta hai jo bilkul
sahi dikhta hai — "invalid state" — lekin galat hai. Wo bug ghanta khaata hai.

Isliye ye file `init.sql` ke `CHECK` constraint ka text padh kar dono list
compare karti hai. Postgres nahi chahiye, `pip install` nahi chahiye.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
QUERIES = REPO_ROOT / "oncall" / "db" / "queries.py"
SCHEMA = REPO_ROOT / "stack" / "oncalldb" / "init.sql"


# ═════════════════════════════════════════════════════════════════════════════
# 0. Helpers — dono taraf se list nikaalna
# ═════════════════════════════════════════════════════════════════════════════


def _module_tuple(name: str, src: str | None = None) -> tuple[str, ...]:
    """`queries.py` ka ek module-level tuple constant, **import kiye bina**.

    🔴 Yahan `from oncall.db import queries` likh kar `queries.STATES` padhna
       aasan hota — lekin wo galat hai. Wo import `oncall.config` ko chhoo leta
       hai (jo `.env` padh sakta hai) aur `oncall.db.pool` ko (jo psycopg
       maangta hai). Is machine pe psycopg install nahi hai. Ek guardrail test
       ko install-state pe depend karna matlab wo test us din chalega hi nahi
       jab uski sabse zyada zarurat hai.

    ✅ `ast.literal_eval` sirf literals evaluate karta hai — koi code nahi
       chalta, koi import nahi hota.
    """
    tree = ast.parse(src if src is not None else QUERIES.read_text())
    for node in tree.body:  # sirf top level — nested scope ke naam nahi chahiye
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        else:
            continue
        if any(t.id == name for t in targets) and node.value is not None:
            value = ast.literal_eval(node.value)
            return tuple(str(v) for v in value)
    raise AssertionError(f"`{name}` module level pe mila hi nahi {QUERIES.name} mein")


def _check_literals(constraint: str) -> set[str]:
    """Ek `CHECK (... IN ('a','b',...))` constraint se saare quoted strings.

    Regex yahan theek hai (AST nahi) kyunki input **SQL** hai, Python nahi —
    aur SQL mein docstring jaisi koi cheez nahi hoti jise ye galti se pakad le.
    """
    return set(re.findall(r"'([^']+)'", constraint))


def _named_check(table: str, constraint_name: str) -> str:
    """`init.sql` se ek naam wala CHECK constraint ka text.

    Balanced-paren counting se, `re` ke ek fixed pattern se nahi: constraint ke
    andar nested `(...)` hote hain (`state IN (...)`), aur `\\)` pe rukne wala
    regex pehle nested close-paren pe hi ruk jaata — aadhi list padhta aur test
    chup-chaap galat pass hota.
    """
    src = SCHEMA.read_text()
    marker = f"CONSTRAINT {constraint_name}"
    start = src.find(marker)
    assert start != -1, (
        f"`{marker}` `init.sql` mein mila hi nahi. Ya constraint ka naam badla "
        f"hai, ya table `{table}` se hata diya gaya — dono case mein "
        "`queries.py` ki copy purani ho chuki hai."
    )
    open_at = src.find("(", start)
    assert open_at != -1, f"`{marker}` ke baad koi `(` nahi"
    depth = 0
    for i in range(open_at, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return src[open_at : i + 1]
    raise AssertionError(f"`{marker}` ka paren band hi nahi hua")


def _execute_calls(src: str) -> list[ast.Call]:
    """Saare `<something>.execute(...)` calls."""
    out: list[ast.Call] = []
    for node in ast.walk(ast.parse(src)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
        ):
            out.append(node)
    return out


def _bad_execute_args(src: str, filename: str = "<planted>") -> list[str]:
    """Wo `execute()` calls jinka pehla argument literal/naam nahi hai.

    Allowed sirf do shapes:
      • `ast.Constant` (str)  → inline literal SQL
      • `ast.Name`            → `_SQL_*` constant ka naam

    Banned: `JoinedStr` (f-string), `BinOp` (`+` ya `%`), `Call` (`.format()`).
    Wajah `docs/TEACH_PHASE4.md` §2a mein hai — SQL injection ka poora mechanism.
    """
    bad: list[str] = []
    for call in _execute_calls(src):
        if not call.args:
            bad.append(f"{filename}:{call.lineno}: execute() bina argument")
            continue
        a0 = call.args[0]
        ok = isinstance(a0, ast.Name) or (
            isinstance(a0, ast.Constant) and isinstance(a0.value, str)
        )
        if not ok:
            bad.append(f"{filename}:{call.lineno}: execute({type(a0).__name__})")
    return bad


# ═════════════════════════════════════════════════════════════════════════════
# 1. Rule #1 — koi f-string `execute()` ke pehle argument pe nahi
# ═════════════════════════════════════════════════════════════════════════════


def test_execute_first_arg_is_literal_or_name() -> None:
    """🔒 `queries.py` ke docstring ka rule #1, machine-checkable.

    Ye wo check hai jo mera pehla draft **fail** kar deta: usme
    `cur.execute(f"SELECT {_INCIDENT_COLUMNS} FROM incidents WHERE id = %s")`
    likha tha. Wo injection-safe tha (constant interpolate ho raha tha), lekin
    ek aadat thi jo ek din user input pe lag jaati.
    """
    bad = _bad_execute_args(
        QUERIES.read_text(), filename=str(QUERIES.relative_to(REPO_ROOT))
    )
    assert not bad, (
        "`execute()` ka pehla argument literal ya `_SQL_*` naam hona chahiye "
        "(f-string/`+`/`.format()` nahi). SQL ko module level pe constant "
        "banao:\n  " + "\n  ".join(bad)
    )


def test_negative_control_execute_check_can_fail() -> None:
    """🔥 Dono taraf se zid — warna is check ka pass matlab-less hai.

    (a) asli f-string pakdi jaaye
    (b) docstring mein quote kiya gaya wahi pattern NA pakda jaaye — kyunki
        `queries.py` khud usko example ki tarah likhti hai, aur agar check
        wahan fail karta toh koi check hata deta (ya documentation hata deta,
        jo isse bhi bura hai)
    """
    planted = "\n".join(
        [
            '"""Rule ka example: `cur.execute(f"SELECT {x} FROM t")` galat hai."""',
            "def f(cur, x):",
            '    cur.execute("SELECT 1")',
            "    cur.execute(_SQL_OK, (x,))",
            '    cur.execute(f"SELECT {x}")           # 🔴 violation 1',
            '    cur.execute("SELECT " + x)           # 🔴 violation 2',
            '    cur.execute("SELECT %s" % x)         # 🔴 violation 3',
            '    cur.execute("SELECT {}".format(x))   # 🔴 violation 4',
        ]
    )
    found = _bad_execute_args(planted)
    assert len(found) == 4, (
        f"negative control fail: 4 violation expected, mili {len(found)} → "
        f"{found}. Matlab AST scan ya docstring ka farq toota hua hai."
    )


def test_queries_file_was_actually_parsed() -> None:
    """🔥 Sabse boring check. Agar `execute()` calls 0 mil rahe hon, toh rule #1
    ka pass ek jhooth hai — wo "koi violation nahi" nahi, "kuch dekha hi nahi"
    hai (`docs/CODEMAP.md` §7c).
    """
    n = len(_execute_calls(QUERIES.read_text()))
    assert n >= 25, (
        f"sirf {n} `execute()` call mile `queries.py` mein — ye file 30+ rakhti "
        "hai. Scan hi nahi chala; rule #1 ka pass matlab-less hai."
    )


# ═════════════════════════════════════════════════════════════════════════════
# 2. Schema drift — `queries.py` ki copy vs `init.sql` ka sach
# ═════════════════════════════════════════════════════════════════════════════


def test_states_match_init_sql() -> None:
    """🔴 `STATES` == `incidents_state_valid` CHECK ki list.

    Ye wahi test hai jiska naam `queries.py` ke `STATES` ke upar likha hai. Wo
    comment kehta hai "drift ka jawab ek test hai, ek comment nahi" — ye file
    us vaade ko poora karti hai.

    Drift ka nuksaan: `queries.py` ka `_check_state()` ek naye state ko reject
    kar deta, aur error message *sahi* dikhta ("invalid state"), isliye koi
    schema ki taraf dekhta hi nahi.
    """
    declared = set(_module_tuple("STATES"))
    in_schema = _check_literals(_named_check("incidents", "incidents_state_valid"))
    assert declared == in_schema, (
        "`STATES` aur `init.sql` ka `incidents_state_valid` alag ho gaye.\n"
        f"  sirf queries.py mein: {sorted(declared - in_schema)}\n"
        f"  sirf init.sql mein:   {sorted(in_schema - declared)}\n"
        "🔴 `init.sql` owner hai (D50) — copy usko follow karti hai, ulta nahi."
    )


def test_terminal_states_match_init_sql() -> None:
    """🔴 `TERMINAL_STATES` == `incidents_closed_consistency` ke andar ki list.

    Ye check `advance_state()`/`close_incident()` ke do-function design ki jaan
    hai. Agar kal ek teesra terminal state (`ABANDONED`) schema mein aaye aur
    `TERMINAL_STATES` mein na aaye, toh `advance_state(to="ABANDONED")` guard se
    nikal jaayega aur DB ka CHECK use `IntegrityError` se rokega — 3 baje raat
    ka wahi bug jisse bachne ke liye do function banaye the.
    """
    declared = set(_module_tuple("TERMINAL_STATES"))
    constraint = _named_check("incidents", "incidents_closed_consistency")
    in_schema = _check_literals(constraint)
    assert declared == in_schema, (
        "`TERMINAL_STATES` aur `incidents_closed_consistency` alag hain.\n"
        f"  sirf queries.py mein: {sorted(declared - in_schema)}\n"
        f"  sirf init.sql mein:   {sorted(in_schema - declared)}"
    )
    # Terminal states `STATES` ka subset hone chahiye — warna `close_incident()`
    # ka `_check_state()` khud hi apne target ko reject kar dega.
    assert declared <= set(_module_tuple("STATES")), (
        f"terminal states {sorted(declared)} `STATES` mein nahi hain"
    )


def test_event_kinds_match_init_sql() -> None:
    """🔴 `EVENT_KINDS` == `events_kind_valid` CHECK ki list.

    `record_event(kind=...)` ka typo (`'state_chagne'`) is copy ki wajah se
    process ke andar `ValueError` banta hai, DB tak jaane se pehle. Wo faayda
    tabhi asli hai jab copy sahi ho.
    """
    declared = set(_module_tuple("EVENT_KINDS"))
    in_schema = _check_literals(_named_check("events", "events_kind_valid"))
    assert declared == in_schema, (
        "`EVENT_KINDS` aur `init.sql` ka `events_kind_valid` alag ho gaye.\n"
        f"  sirf queries.py mein: {sorted(declared - in_schema)}\n"
        f"  sirf init.sql mein:   {sorted(in_schema - declared)}"
    )


def test_negative_control_check_parser_can_fail() -> None:
    """🔥 `_named_check()` + `_check_literals()` sach mein padh rahe hain?

    Agar `_named_check()` khaali string de (marker badal gaya) toh
    `_check_literals()` khaali set deta, aur upar wale teen test **tab bhi
    pass** kar sakte the — bas jab `STATES` bhi khaali ho. Isliye yahan direct
    zid: schema se nikli list ka size expected range mein ho.

    🔴 Ye ginti khud ek claim hai: `docs/TDD.md` 10 states aur 10 event kinds
       bolta hai. Agar ye test fail ho, toh ya schema badla hai ya doc jhooth
       bol raha hai — dono `CLAUDE.md` §2 ke hisaab se same change mein fix
       hone chahiye.
    """
    states = _check_literals(_named_check("incidents", "incidents_state_valid"))
    kinds = _check_literals(_named_check("events", "events_kind_valid"))
    assert len(states) == 10, f"init.sql mein {len(states)} states mile, 10 expected"
    assert len(kinds) == 10, f"init.sql mein {len(kinds)} event kinds mile, 10 expected"
    assert "DETECTED" in states and "RECOVERED" in states, (
        f"parser ne bakwaas padha: {sorted(states)}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# 3. Leak guard aur psycopg-3 ki aadat
# ═════════════════════════════════════════════════════════════════════════════


def test_runbook_columns_hide_ground_truth() -> None:
    """🔴 `CLAUDE.md` §3 — ground truth agent ko dikhni nahi chahiye.

    `RUNBOOK_COLUMNS` hi wo ek jagah hai jahan se runbook SELECTs banti hain
    (Phase 6 ka `retrieval/search.py` bhi isi se banayega). Isliye ye ek list
    check karna poore retrieval path ko cover karta hai.

    `embedding` bhi banned hai, lekin dusri wajah se: wo ~1.5 KB per row hai
    aur Python tak aane ki zarurat nahi (comparison Postgres ke andar `<=>` se
    hota hai). Wo performance hai; `is_relevant` security hai.
    """
    cols = set(_module_tuple("RUNBOOK_COLUMNS"))
    assert "is_relevant" not in cols, (
        "🔴 `is_relevant` `RUNBOOK_COLUMNS` mein aa gaya — wo N4 ki ground truth "
        "hai aur agent ko dikhni nahi chahiye (`CLAUDE.md` §3). Iska matlab N4 "
        "ka poora measurement bekaar."
    )
    assert "embedding" not in cols, (
        "`embedding` `RUNBOOK_COLUMNS` mein aa gaya — 1.5 KB/row bina zarurat"
    )
    # Provenance columns hone chahiye — `CLAUDE.md` §3 ka licence rule.
    for needed in ("id", "title", "body", "source", "licence", "fetched_at"):
        assert needed in cols, f"`{needed}` `RUNBOOK_COLUMNS` se gayab hai"


def test_no_explicit_commit() -> None:
    """🔴 psycopg 3 mein `with get_conn()` block hi commit karta hai.

    `conn.commit()` likhna psycopg-2 ki aadat hai. Wo galat nahi *dikhta*,
    lekin do nuksaan karta hai: (1) transaction jaldi khatam ho jaata hai, toh
    `open_incident()` ke teen INSERT alag transaction ban jaate hain aur
    "atomic" ka vaada toot jaata hai; (2) `claim_job()` mein `FOR UPDATE` ka
    lock waqt se pehle chhoot jaata hai, aur do worker ek job utha lete hain.

    Detail: `docs/TEACH_PHASE1.md` §2h aur `TEACH_PHASE4.md` §4.
    """
    hits: list[str] = []
    for node in ast.walk(ast.parse(QUERIES.read_text())):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("commit", "rollback")
        ):
            hits.append(f"line {node.lineno}: .{node.func.attr}()")
    assert not hits, (
        "psycopg 3 mein `with` block khud commit/rollback karta hai — ye call "
        "transaction boundary tod deta hai:\n  " + "\n  ".join(hits)
    )


def test_no_chaos_import() -> None:
    """🔴 `CLAUDE.md` §3 — `oncall/` `chaos/` ko import nahi kar sakta.

    `chaos/faults.py` mein `correct_action` hai. Agar wo is process mein aa
    jaaye, toh koi bhi (aaj nahi, chhe mahine baad) usko "sirf logging ke liye"
    padh lega, aur measurement chup-chaap contaminate ho jaayega.
    """
    bad: list[str] = []
    for node in ast.walk(ast.parse(QUERIES.read_text())):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if a.name.split(".")[0] == "chaos"]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "chaos":
                bad.append(node.module or "chaos")
    assert not bad, f"🔴 `chaos/` se import mila: {bad} (`CLAUDE.md` §3)"


def test_every_sql_constant_is_used() -> None:
    """Har `_SQL_*` constant kahin **padha** jaata hai.

    Ek unused `_SQL_*` do cheezon mein se ek hai: (a) dead code, ya (b) ek
    query jise use karna bhool gaye — aur (b) ka matlab hai ki koi function
    chup-chaap adhoora hai. Dono review mein aankh se nahi dikhte.

    ⚠️ Mera pehla draft yahan `execute()` ke direct arguments ginta tha, aur
       chaar constants pe **fail** hua — `_SQL_ADVANCE_STATE`,
       `_SQL_CLOSE_INCIDENT`, `_SQL_LIST_INCIDENTS`, `_SQL_LIST_OPEN_INCIDENTS`.
       Chaaron sahi tarah use ho rahe the, bas indirect: do `_transition()` ko
       argument ki tarah jaate hain, do ek `if/else` se `sql` local mein aate
       hain. Check galat tha, code nahi. Isliye ab ye "kahin bhi read hua" ginta
       hai — usage ka aakhri consumer dhoondhna is check ka kaam nahi hai.
    """
    src = QUERIES.read_text()
    tree = ast.parse(src)
    declared = {
        t.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name) and t.id.startswith("_SQL_")
    }
    read = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    unused = declared - read
    assert not unused, (
        f"ye `_SQL_*` constants kahin use nahi hote: {sorted(unused)}. Ya dead "
        "code hai, ya koi function adhoora reh gaya."
    )
    assert len(declared) >= 20, (
        f"sirf {len(declared)} `_SQL_*` constants mile — scan hi nahi chala"
    )


def test_execute_name_args_resolve_to_sql_constants() -> None:
    """🔒 Rule #1 ka wo chhed jo `ast.Name` allow karne se banta hai.

    Rule #1 `execute(<Name>)` ko theek maanta hai — kyunki `_SQL_*` constants
    aise hi jaate hain. Lekin `sql = f"SELECT ... {user}"` likh kar phir
    `cur.execute(sql)` karna bhi ek `Name` hai. Rule #1 usko green de deta.

    Isliye: jo bhi naam `execute()` ko jaata hai, wo ya `_SQL_` se shuru hoga,
    ya uski **har assignment** ek `_SQL_*` naam pe resolve honi chahiye
    (`if/else` allowed — `list_incidents()` wahi karta hai). Function ka
    parameter (jaise `_transition(sql=...)`) allowed hai, kyunki uske caller
    ka argument khud isi check se guzarta hai.
    """
    tree = ast.parse(QUERIES.read_text())

    def _is_sql_name(node: ast.expr) -> bool:
        return isinstance(node, ast.Name) and node.id.startswith("_SQL_")

    # Kaun-kaun naam function parameter hai — unko trusted maana jaata hai.
    params: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs]:
                params.add(arg.arg)

    # Local `sql = ...` jaisi assignments ka RHS.
    assigned: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and node.value is not None:
            for t in node.targets:
                if isinstance(t, ast.Name):
                    assigned.setdefault(t.id, []).append(node.value)

    bad: list[str] = []
    for call in _execute_calls(QUERIES.read_text()):
        if not call.args or not isinstance(call.args[0], ast.Name):
            continue
        name = call.args[0].id
        if name.startswith("_SQL_"):
            continue
        if name in params:
            continue  # caller ka argument khud check hota hai
        rhs = assigned.get(name)
        if not rhs:
            bad.append(f"line {call.lineno}: `{name}` kahan se aaya, pata nahi")
            continue
        for value in rhs:
            if _is_sql_name(value):
                continue
            if isinstance(value, ast.IfExp) and all(
                _is_sql_name(v) for v in (value.body, value.orelse)
            ):
                continue
            bad.append(
                f"line {value.lineno}: `{name}` ek `_SQL_*` constant nahi hai "
                f"({type(value).__name__})"
            )
    assert not bad, (
        "`execute()` ko diya gaya naam `_SQL_*` constant pe resolve hona chahiye "
        "— warna rule #1 ke neeche se f-string nikal jaata hai:\n  "
        + "\n  ".join(bad)
    )


# ═════════════════════════════════════════════════════════════════════════════
# 4. Runner — pytest ke bina (is machine pe wo install nahi hai)
# ═════════════════════════════════════════════════════════════════════════════
# 🔴 Ek check jo `_checks` mein nahi hai, wo KABHI nahi chalti — aur usko koi
#    error nahi batata. Naya `def test_...` likhne pe usko yahan bhi jodna
#    padta hai. (Yahi `tests/test_schema.py` aur `test_no_shell.py` mein bhi.)
if __name__ == "__main__":
    _checks = (
        test_queries_file_was_actually_parsed,
        test_negative_control_execute_check_can_fail,
        test_execute_first_arg_is_literal_or_name,
        test_execute_name_args_resolve_to_sql_constants,
        test_negative_control_check_parser_can_fail,
        test_states_match_init_sql,
        test_terminal_states_match_init_sql,
        test_event_kinds_match_init_sql,
        test_runbook_columns_hide_ground_truth,
        test_no_explicit_commit,
        test_no_chaos_import,
        test_every_sql_constant_is_used,
    )
    _failed = 0
    print(f"file:   {QUERIES.relative_to(REPO_ROOT)}")
    print(f"schema: {SCHEMA.relative_to(REPO_ROOT)}\n")
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
    print(f"saare {len(_checks)} queries check pass hue (bina DB, bina psycopg)")
