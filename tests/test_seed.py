"""
tests/test_seed.py — `scripts/seed_shopdb.py` ke liye tests.

─────────────────────────────────────────────────────────────────────────────
🔴 IN TESTS KI SABSE ZARURI BAAT: inme koi DATABASE NAHI CHAHIYE.
─────────────────────────────────────────────────────────────────────────────
Seed script ka poora "kya banega" wala logic `make_rows()` mein hai, aur wo ek
PURE function hai — input mein n, seed, now; output mein rows. Na koi network,
na koi disk, na koi clock (kyunki `now` ek parameter hai).

Iska faayda: `make test` bina `make up` ke chalta hai. Ek test suite jise
chalane ke liye 4 containers up karna pade, wo suite din mein 2 baar chalti hai.
Jo bina setup chalti hai, wo har change pe chalti hai.

─────────────────────────────────────────────────────────────────────────────
🔥 2026-08-22 — YE FILE PEHLE **KISI BHI** MACHINE PE CHAL NAHI RAHI THI
─────────────────────────────────────────────────────────────────────────────
Upar wala paragraph sach tha (database ki zarurat nahi) aur phir bhi ye file
zero baar chali thi. Do wajah, dono measure ki gayi:

  1. `import pytest` top pe tha, `@pytest.mark.parametrize` aur `monkeypatch`/
     `capsys` fixtures use hote the. Is machine pe pytest install NAHI hai
     (network sirf do host allow karta hai, `pip install` possible nahi).
     Baaki chaar test files (`test_broker`, `test_inject`, `test_no_shell`,
     `test_schema`) exactly isi wajah se apna `__main__` runner rakhti hain —
     ye paanchwi us convention se chhoot gayi thi.

  2. Isse bhi bura: `scripts/seed_shopdb.py` import time pe `sys.exit()` karta
     tha jab psycopg missing ho. 🔴 `SystemExit` `Exception` se inherit nahi
     karta, toh neeche wala `importlib` load poore process ko maar deta —
     collect hone se pehle. Wo D51 mein fix hua.

Ab: **koi pytest nahi**. Ye file stdlib se chalti hai (`contextlib`, `io`,
`importlib`), aur pytest ke saath BHI chalti hai — plain `def test_*` functions
jo koi fixture nahi maangte, aur ek `_Skip` exception jo faker hone par kabhi
uthta hi nahi (aur pytest hone ka matlab hai `make install` chala hai, yaani
faker hai).

─────────────────────────────────────────────────────────────────────────────
🔴 SKIP HAMESHA LOUD HAI — kyunki chup-chaap skip fail se zyada khatarnaak hai
─────────────────────────────────────────────────────────────────────────────
18 checks hain. 13 unme se `make_rows()` call karti hain, aur `make_rows()` ko
`faker` chahiye (naam generate karne ke liye). Faker ke bina wo 13 chal nahi
sakti — baaki 5 (ek constant check, `summarise([])`, do CLI validation, aur ek
meta-check) kisi bhi machine pe chalti hain.

Do galat raaste, dono reject:
  ❌ crash — phir baaki 5 bhi nahi chalti, aur "test file toot gayi" lagta hai.
  ❌ chup-chaap pass — sabse bura. Ek check jo kuch dekhti nahi aur "ok" bolti
     hai, wo fail hone se zyada khatarnaak hai (`docs/CODEMAP.md` §7c).

Isliye: `_require_faker()` ek `_Skip` uthata hai, runner usko GIN kar
`SKIP` print karta hai, aur aakhir mein "X pass · Y skip · Z fail" dikhata hai.
Aur agar **ek bhi** test na chale, runner non-zero exit karta hai — kyunki 0
checks ka "sab theek" ek jhooth hai.

✅ 2026-08-22, is machine pe measure kiya:
    python3 tests/test_seed.py                    →  5 pass · 13 skip · 0 fail
    PYTHONPATH=<faker-stub> python3 …             → 18 pass ·  0 skip · 0 fail
  Doosri line ka matlab: wo 13 skip hone wali checks ka code MURDA nahi hai —
  ek minimal stub faker ke saath poori chali aur pass hui. (Stub sirf
  verification ke liye tha, repo mein nahi hai.)

─────────────────────────────────────────────────────────────────────────────
🔴 YE TESTS ASLI CONSTRAINTS KE KHILAAF CHECK KARTE HAIN, APNE HISAAB SE NAHI
─────────────────────────────────────────────────────────────────────────────
`ALLOWED_STATUSES` aur `numeric(10,2)` ki limits `stack/shopdb/init.sql` ke
CHECK constraints se aayi hain. Matlab agar koi kal `STATUS_WEIGHTS` mein
paanchwa status add kare, ye test FAIL hoga — bilkul waise hi jaise Postgres
`INSERT` reject karta. Test ka kaam wahi galti pehle pakadna hai, `make up`
ke 5 minute baad nahi.

⚠️ Ye duplication hai: statuses do jagah likhe hain (init.sql aur yahan).
   Uska theek fix hota ki test init.sql ko parse kare — lekin SQL parse karna
   is project ke liye bekaar mehnat hai. Isliye duplication accept kiya hai,
   ek comment ke saath. Ye test khud us duplication ka guard hai.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

# ── Script ko module ki tarah load karna ─────────────────────────────────────
#
# 🔴 `from scripts.seed_shopdb import make_rows` kaam nahi karta, aur wajah
#    seekhne layak hai: `scripts/` mein `__init__.py` nahi hai — wo ek package
#    nahi, executable scripts ka folder hai. Aur usme `__init__.py` daalna galat
#    hota: phir wo importable package ban jaata aur `oncall/` ke saath confuse
#    hota (do jagah code, ek hi naamespace).
#
#    `importlib` ka ye 3-line pattern ek FILE ko module ki tarah load karta hai,
#    package structure ki zarurat ke bina. Ye standard library ka hissa hai,
#    koi hack nahi.
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "seed_shopdb.py"
_spec = importlib.util.spec_from_file_location("seed_shopdb", _SCRIPT)
assert _spec is not None and _spec.loader is not None
seed_shopdb = importlib.util.module_from_spec(_spec)
sys.modules["seed_shopdb"] = seed_shopdb
_spec.loader.exec_module(seed_shopdb)

make_rows = seed_shopdb.make_rows
summarise = seed_shopdb.summarise
STATUS_WEIGHTS = seed_shopdb.STATUS_WEIGHTS


# ── Skip machinery ───────────────────────────────────────────────────────────


class _Skip(Exception):
    """Ek test ne khud kaha "main is machine pe chal nahi sakti".

    🔴 Ye `AssertionError` se ALAG class hai, aur wahi poora point hai. Runner
       dono ko alag ginta hai: fail = "code galat hai", skip = "dependency nahi
       hai". Ek hi class use karne se dono ek dikhte, aur "13 fail" ek jhoothi
       alarm hoti jabki asli baat "13 chali hi nahi" hai.
    """


def _require_faker() -> None:
    """Ye line un 13 tests ke top pe hai jinhe `make_rows()` chahiye.

    Ek decorator zyada "saaf" dikhta, lekin ye 1 line explicit hai aur
    greppable hai — ✅ 2026-08-22:
        grep -c '^    _require_faker()' tests/test_seed.py   →  13
    CLAUDE.md §1: 20 samajh mein aane wali lines > 5 clever lines.
    """
    if not seed_shopdb._HAS_FAKER:
        raise _Skip("faker install nahi hai (`make install`) — make_rows() nahi chal sakta")


# ── Constants jo init.sql se copy hui hain ───────────────────────────────────
#
# ⚠️ Ye duplication hai (docstring mein iska defence likha hai). `stack/shopdb/
#    init.sql` ka CHECK constraint bilkul yahi 4 naam allow karta hai.
ALLOWED_STATUSES = frozenset({"pending", "paid", "shipped", "cancelled"})

# `numeric(10,2)` = kul 10 digits, jinme 2 decimal ke baad. Toh sabse bada
# possible value 99999999.99 hai. Script AMOUNT_MAX = 99999.99 pe clamp karti
# hai, jo isse kaafi neeche hai — jaan-boojh kar, headroom ke liye.
NUMERIC_MAX = Decimal("99999999.99")

# 🔴 Ek FIXED anchor, `datetime.now()` nahi. Iske bina determinism test khud
#    non-deterministic ho jaata (wahi bug jo `make_rows` mein tha).
ANCHOR = datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc)


# ═════════════════════════════════════════════════════════════════════════════
# §1 — DETERMINISM: same seed = same rows
# ═════════════════════════════════════════════════════════════════════════════
# Kyun ye pehla section hai: "same seed = same rows" script ki sabse badi
# claim hai (wo terminal pe bhi print hoti hai). Ek aisi claim jiska test na
# ho, wo ek comment hai — guarantee nahi.


def test_same_seed_same_anchor_gives_identical_rows():
    _require_faker()
    a = make_rows(50, seed=42, now=ANCHOR)
    b = make_rows(50, seed=42, now=ANCHOR)
    # `==` tuples pe field-by-field chalta hai, aur Decimal/datetime dono
    # `__eq__` sahi define karte hain — toh ye poori list ka exact compare hai.
    assert a == b, "same seed + same anchor ne alag rows di — determinism toota"


def test_different_seed_gives_different_rows():
    _require_faker()
    a = make_rows(50, seed=42, now=ANCHOR)
    b = make_rows(50, seed=43, now=ANCHOR)
    # 🔴 Ye test upar wale ka NEGATIVE CONTROL hai. Iske bina ek `make_rows` jo
    #    hamesha wahi 50 rows lautata (seed ignore karke) bhi pass ho jaata.
    #    Ek test jo har implementation pe pass ho, wo kuch measure nahi karti.
    assert a != b, "do alag seed ne bilkul same rows di — seed ka koi asar nahi"


def test_only_created_at_depends_on_the_clock():
    _require_faker()
    later = ANCHOR + timedelta(days=1)
    a = make_rows(20, seed=42, now=ANCHOR)
    b = make_rows(20, seed=42, now=later)

    # customer/amount/status seed se aate hain → clock badalne pe same rehne
    # chahiye. Sirf created_at shift honi chahiye. Ye exactly wo bug pin karta
    # hai jo pehle tha (hidden `datetime.now()` andar).
    assert [(r[0], r[1], r[2]) for r in a] == [(r[0], r[1], r[2]) for r in b], (
        "clock badalne se customer/amount/status bhi badal gaye — koi hidden "
        "clock dependency hai"
    )
    for row_a, row_b in zip(a, b):
        assert row_b[3] - row_a[3] == timedelta(days=1), (
            f"created_at exactly 1 din shift nahi hui: {row_a[3]} → {row_b[3]}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# §2 — ROWS DATABASE KE CONSTRAINTS KE HISAAB SE HAIN
# ═════════════════════════════════════════════════════════════════════════════
# Ye section `stack/shopdb/init.sql` ki jagah khada hai. Wahan jo CHECK
# constraints hain, wahi yahan Python mein check hote hain — taaki galti
# `make up` ke 5 minute baad na pakdi jaaye.


def test_every_status_passes_the_init_sql_check():
    _require_faker()
    rows = make_rows(500, seed=7, now=ANCHOR)
    seen = {status for _, _, status, _ in rows}
    extra = seen - ALLOWED_STATUSES
    assert not extra, f"init.sql ka CHECK in statuses ko reject karega: {sorted(extra)}"


def test_status_weights_match_the_allowed_set_exactly():
    # 🔴 Ye test `make_rows()` ko call NAHI karta — sirf constant padhta hai.
    #    Isliye ye faker ke bina bhi chalta hai, aur yahi is file ka sabse
    #    zaruri guard hai: koi kal `STATUS_WEIGHTS` mein "refunded" add kare,
    #    ye test us commit pe hi fail hoga.
    assert set(STATUS_WEIGHTS) == set(ALLOWED_STATUSES), (
        f"STATUS_WEIGHTS ({sorted(STATUS_WEIGHTS)}) aur init.sql ka CHECK "
        f"({sorted(ALLOWED_STATUSES)}) alag ho gaye"
    )
    assert all(w > 0 for w in STATUS_WEIGHTS.values()), (
        "weight 0 ka matlab wo status kabhi generate nahi hoga — phir usko "
        "list mein rakhna jhooth hai"
    )


def test_amounts_satisfy_amount_check_and_numeric_precision():
    _require_faker()
    rows = make_rows(1_000, seed=11, now=ANCHOR)
    for _, amount, _, _ in rows:
        # init.sql: CHECK (amount >= 0)
        assert amount >= 0, f"negative amount: {amount}"
        # numeric(10,2): 2 se zyada decimal places allowed nahi
        exponent = amount.as_tuple().exponent
        assert isinstance(exponent, int) and exponent >= -2, (
            f"{amount} mein 2 se zyada decimal places hain — numeric(10,2) round kar dega"
        )
        assert amount <= NUMERIC_MAX, f"{amount} numeric(10,2) mein fit nahi hoga"


def test_created_at_is_timezone_aware_and_inside_the_window():
    _require_faker()
    rows = make_rows(300, seed=13, now=ANCHOR)
    window_start = ANCHOR - timedelta(days=seed_shopdb.DAYS_BACK)
    for _, _, _, created_at in rows:
        # 🔴 `tzinfo is not None` — column `timestamptz` hai. Ek naive datetime
        #    ko psycopg server ke timezone mein maan leta, aur phir "order 5:30
        #    baje aaya" ka jawab machine pe depend karta. Ye ek asli production
        #    bug ki shakal hai, isliye test isko pin karta hai.
        assert created_at.tzinfo is not None, f"naive datetime mila: {created_at}"
        assert window_start <= created_at <= ANCHOR, (
            f"{created_at} 90-din ki window ({window_start} → {ANCHOR}) ke bahar hai"
        )


def test_row_shape_matches_the_copy_column_list():
    _require_faker()
    rows = make_rows(3, seed=17, now=ANCHOR)
    # 🔴 `COPY orders (customer, amount, status, created_at) FROM STDIN` — 4
    #    column, isi order mein. Ek 5-tuple ya galat order COPY ke waqt fail
    #    hota, aur wo failure ek live database maangta. Yahan wo shape ek
    #    stdlib test se pin ho jaati hai.
    for row in rows:
        assert isinstance(row, tuple) and len(row) == 4, f"row 4-tuple nahi hai: {row!r}"
        customer, amount, status, created_at = row
        assert isinstance(customer, str) and customer.strip(), f"khaali customer: {customer!r}"
        # 🔴 Decimal, float NAHI. float ka binary representation paise ke liye
        #    galat hai (0.1 + 0.2 != 0.3), aur `numeric` column ka poora point
        #    exact decimal arithmetic hai.
        assert isinstance(amount, Decimal), f"amount {type(amount).__name__} hai, Decimal nahi"
        assert isinstance(status, str), f"status {type(status).__name__} hai"
        assert isinstance(created_at, datetime), f"created_at {type(created_at).__name__} hai"


# ═════════════════════════════════════════════════════════════════════════════
# §3 — DISTRIBUTION: data "realistic" hai, uniform nahi
# ═════════════════════════════════════════════════════════════════════════════
# In do tests ka maqsad statistics nahi hai — ye pin karte hain ki koi galti se
# `lognormvariate` ko `uniform` se replace na kar de. Thresholds jaan-boojh kar
# DHEELE hain (nazuk test roz-roz fail hoke ignore hona shuru ho jaate hain).


def test_amount_distribution_has_a_long_tail():
    _require_faker()
    rows = make_rows(2_000, seed=19, now=ANCHOR)
    amounts = sorted(amt for _, amt, _, _ in rows)
    median = amounts[len(amounts) // 2]
    p99 = amounts[int(len(amounts) * 0.99)]

    # Lognormal (mu=6.2, sigma=0.9) mein p99/median ≈ 8. `random.uniform(1,5000)`
    # mein ye ratio ≈ 2 hota. Threshold 3 dono ko aaram se alag karta hai aur
    # kisi bhi seed pe flaky nahi hai.
    assert p99 >= median * 3, (
        f"p99 ({p99}) median ({median}) se sirf {p99 / median:.1f}x hai — ye "
        "uniform distribution jaisa lagta hai, lognormal jaisa nahi"
    )
    assert amounts[0] >= seed_shopdb.AMOUNT_MIN, f"clamp toota: {amounts[0]}"
    assert amounts[-1] <= seed_shopdb.AMOUNT_MAX, f"clamp toota: {amounts[-1]}"


def test_status_mix_is_not_uniform():
    _require_faker()
    rows = make_rows(2_000, seed=23, now=ANCHOR)
    counts: dict[str, int] = {}
    for _, _, status, _ in rows:
        counts[status] = counts.get(status, 0) + 1

    # Weights 45/35/15/5 hain — toh sabse common status sabse kam wale se
    # kaafi zyada hona chahiye. Kyun ye maayne rakhta hai: `orders_status_idx`
    # ka faayda hi selectivity pe khada hai. Har status equally common ho toh
    # index bekaar hai, aur "index ne query fast ki" ka demo jhootha ho jaata.
    assert len(counts) == len(ALLOWED_STATUSES), (
        f"2,000 rows mein har status aana chahiye tha, mile: {sorted(counts)}"
    )
    assert max(counts.values()) >= min(counts.values()) * 3, (
        f"status mix lagbhag uniform hai ({counts}) — weights kaam nahi kar rahe"
    )


def test_make_rows_returns_exactly_n_rows():
    _require_faker()
    # 🔴 Pehle ye `@pytest.mark.parametrize("n", [1, 2, 17, 500])` tha. Ab ek
    #    loop hai. Trade-off imaandari se: parametrize ke saath pytest 4 alag
    #    naam wale cases dikhata (`[1]`, `[2]`, …) aur ek fail hone pe baaki
    #    3 phir bhi chalte. Loop mein pehla fail poori test rok deta hai.
    #    Badle mein ye test IS machine pe chalta hai, jahan pytest nahi hai —
    #    aur ek test jo chalta hai, ek behtar-named test se zyada keemti hai.
    #    Diagnostic wapas laane ke liye assert message mein `n` likha hai.
    for n in (1, 2, 17, 500):
        rows = make_rows(n, seed=42, now=ANCHOR)
        assert len(rows) == n, f"n={n} maanga, {len(rows)} rows mili"


# ═════════════════════════════════════════════════════════════════════════════
# §4 — summarise(): dry-run ka output
# ═════════════════════════════════════════════════════════════════════════════


def test_summarise_handles_empty_input():
    # Faker ki zarurat nahi — khaali list khud banayi hai. Ye edge case isliye
    # test hoti hai kyunki `summarise` ke andar `amounts[0]` aur `len(rows)` se
    # division hai; khaali list pe wo IndexError/ZeroDivisionError dete.
    assert summarise([]) == "  (koi row nahi)"


def test_summarise_reports_every_status():
    _require_faker()
    rows = make_rows(200, seed=29, now=ANCHOR)
    out = summarise(rows)
    for status in ALLOWED_STATUSES:
        # 🔴 `summarise` jaan-boojh kar 0-count status bhi print karta hai
        #    (`STATUS_WEIGHTS` pe loop karta hai, `counts` pe nahi). Wajah: ek
        #    gayab line "0 the" se zyada confusing hoti — aur is test ke bina
        #    koi usko `counts` pe loop mein badal sakta tha.
        assert status in out, f"summarise() ne '{status}' ka zikr hi nahi kiya:\n{out}"
    assert f"{len(rows):,}" in out, "row count summary mein nahi hai"


# ═════════════════════════════════════════════════════════════════════════════
# §5 — CLI ka behaviour (yahan pytest ke `monkeypatch`/`capsys` ki jagah stdlib)
# ═════════════════════════════════════════════════════════════════════════════


@contextlib.contextmanager
def _patched(obj: object, name: str, value: object):
    """`monkeypatch.setattr` ka 6-line stdlib version.

    🔴 `finally` mein purani value wapas lagti hai. Iske bina ek test ka
       `sys.argv` agle test mein leak karta — aur leak hone wala global state
       wo cheez hai jise debug karna sabse mushkil hota hai.
    """
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


def _run_cli(argv: list[str], **patches: object) -> tuple[int, str, str]:
    """`seed_shopdb.main()` ko chalata hai aur (rc, stdout, stderr) lautata hai.

    Ye `capsys` ki jagah hai. `contextlib.redirect_stdout` ek stdlib tool hai
    jo `sys.stdout` ko temporarily badal deta hai — aur `print()` har call pe
    `sys.stdout` dobara dekhta hai, isliye redirect kaam karta hai.

    🔴 Redirect `main()` ke CHALNE SE PEHLE lagta hai, baad mein nahi. Ek baar
       output terminal pe chala gaya toh use wapas nahi pakad sakte.

    `**patches` se koi bhi module-level naam replace kiya ja sakta hai —
    jaise `write_rows=_boom`, jisse ye saabit hota hai ki dry-run us function
    ko chhoota bhi nahi.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.ExitStack() as stack:
        stack.enter_context(_patched(sys, "argv", ["seed_shopdb.py", *argv]))
        for name, value in patches.items():
            stack.enter_context(_patched(seed_shopdb, name, value))
        stack.enter_context(contextlib.redirect_stdout(out))
        stack.enter_context(contextlib.redirect_stderr(err))
        rc = seed_shopdb.main()
    return rc, out.getvalue(), err.getvalue()


def test_dry_run_is_the_default_and_never_writes():
    _require_faker()

    def _boom(*_a: object, **_kw: object) -> int:
        # 🔴 Ye "booby trap" pattern hai: agar dry-run galti se write path pe
        #    chala gaya, test ek saaf message ke saath fail hoti hai — chup-chaap
        #    pass nahi hoti. `None` return karne wala stub isse bahut kamzor
        #    hota (galti chhup jaati).
        raise AssertionError("🔴 dry-run ne write_rows() call kiya — CLAUDE.md §3 ka ulta")

    # --rows chhota rakha hai: 5,000 rows generate karne mein waqt lagta hai aur
    # is test ka sawaal rows ki ginti nahi, write hua ya nahi hai.
    rc, out, err = _run_cli(["--rows", "10"], write_rows=_boom)
    assert rc == 0, f"dry-run non-zero exit kiya (rc={rc}), stderr:\n{err}"
    assert "DRY RUN" in out, f"'DRY RUN' banner output mein nahi:\n{out}"
    assert "database chhua bhi nahi gaya" in out, f"dry-run ka confirmation gayab:\n{out}"


def test_truncate_without_write_is_rejected_loudly():
    # 🔴 Ye test faker ke bina bhi chalti hai, aur ye ittefaq nahi hai:
    #    `main()` mein flag-validation dependency-check se PEHLE hai. Matlab ek
    #    galat command bina venv ke bhi sahi jawab deti hai. D51 ka yahi faayda.
    rc, out, err = _run_cli(["--truncate", "--rows", "10"])
    assert rc == 1, f"--truncate bina --write ke pass ho gaya (rc={rc})"
    # 🔴 Message stderr pe hona chahiye, stdout pe nahi. Wajah: `... | grep` ya
    #    `>file` karne wale ko error dikhna chahiye, chhupna nahi.
    assert "--write" in err, f"error message ne fix nahi bataya:\n{err}"
    assert out == "" or "DRY RUN" not in out, "reject hone ke baad bhi kaam kar diya"


def test_zero_rows_is_rejected():
    rc, _out, err = _run_cli(["--rows", "0"])
    assert rc == 1, f"--rows 0 accept ho gaya (rc={rc})"
    assert "--rows" in err, f"error message ne wajah nahi batayi:\n{err}"


def test_password_is_masked_in_output():
    _require_faker()
    rc, out, err = _run_cli(
        ["--rows", "10", "--dsn", "postgresql://shop:supersecret@127.0.0.1:5432/shop"]
    )
    assert rc == 0, f"dry-run fail hua (rc={rc}):\n{err}"
    # 🔴 Ye check POORE output pe hai — banner, `current_state()` ki line, sab.
    #    Jaan-boojh kar: agar kal koi error message DSN ko poora print kar de,
    #    ye test tab bhi fail hogi. Ek script jiska output paste/screenshot
    #    hota hai, usme password ek asli leak hai.
    assert "supersecret" not in out + err, "🔴 password output mein leak hua"
    assert "shop:***@" in out, f"masked DSN dikhna chahiye tha:\n{out}"


# ═════════════════════════════════════════════════════════════════════════════
# §6 — RUNNER KA APNA GUARD (meta-test)
# ═════════════════════════════════════════════════════════════════════════════


def test_runner_lists_every_test_in_this_file():
    """🔴 Ek `def test_*` jo `_checks` mein nahi hai, wo KABHI nahi chalti.

    pytest tests ko naam se khud dhoond leta hai; ye runner nahi — usko list
    di jaati hai. Toh sabse aasan galti ye hai: naya test likh diya, list mein
    daalna bhool gaye. Result: file mein coverage DIKHTI hai, chalti nahi.
    `tests/test_broker.py` mein bilkul yahi guard hai — ye us convention ka
    hissa hai, mera naya idea nahi.

    Ye check `_checks` ko run-time pe padhta hai (jo neeche define hota hai).
    Python mein wo theek hai: global naam function ke CALL pe dekha jaata hai,
    define hone pe nahi.
    """
    defined = {
        name
        for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    }
    listed = {check.__name__ for check in _checks}

    missing = defined - listed
    assert not missing, (
        f"ye test(s) file mein hain lekin `_checks` mein nahi — inhe koi nahi "
        f"chalata: {sorted(missing)}"
    )
    # Ulta case bhi: ek naam jo list mein hai par function delete ho gaya. Wo
    # `NameError` deta hai import time pe, isliye us pe assert ki zarurat nahi —
    # lekin duplicate entry chup-chaap ek test do baar chala deti hai.
    assert len(listed) == len(_checks), "`_checks` mein ek hi test do baar hai"


# ═════════════════════════════════════════════════════════════════════════════
# 🔴 RUNNER — ye file bina pytest chalti hai
# ═════════════════════════════════════════════════════════════════════════════
#     python3 tests/test_seed.py
#
# Convention baaki chaar test files se aayi hai (`test_broker`, `test_inject`,
# `test_no_shell`, `test_schema`): ek `_checks` tuple + `if __name__ ==
# "__main__"` runner + `ok`/`FAIL` labels. ✅ 2026-08-22 pe chaaron padh kar
# confirm kiya.
#
# Do jaan-boojh kar kiye gaye farq, kyunki is file ki zarurat alag hai:
#   1. SKIP ki ginti — baaki chaar ko kisi optional dependency ki zarurat nahi,
#      is file ko faker chahiye. Skip ko fail ke saath milana jhooth hota.
#   2. `_checks` module level pe hai (jaise `test_inject.py` mein), `__main__`
#      ke andar nahi. Faayda: `_main()` ko bahar se call kar ke uske "0 checks
#      chali = fail" guard ko KHUD test kiya ja sakta hai — ✅ 2026-08-22 pe
#      kiya, rc=1 aaya. `__main__` ke andar rakhne se wo verify hi nahi hota.
_checks = (
    # §1 determinism
    test_same_seed_same_anchor_gives_identical_rows,
    test_different_seed_gives_different_rows,
    test_only_created_at_depends_on_the_clock,
    # §2 constraints
    test_every_status_passes_the_init_sql_check,
    test_status_weights_match_the_allowed_set_exactly,
    test_amounts_satisfy_amount_check_and_numeric_precision,
    test_created_at_is_timezone_aware_and_inside_the_window,
    test_row_shape_matches_the_copy_column_list,
    # §3 distribution
    test_amount_distribution_has_a_long_tail,
    test_status_mix_is_not_uniform,
    test_make_rows_returns_exactly_n_rows,
    # §4 summarise
    test_summarise_handles_empty_input,
    test_summarise_reports_every_status,
    # §5 CLI
    test_dry_run_is_the_default_and_never_writes,
    test_truncate_without_write_is_rejected_loudly,
    test_zero_rows_is_rejected,
    test_password_is_masked_in_output,
    # §6 meta — ye khud check karta hai ki upar wali list poori hai
    test_runner_lists_every_test_in_this_file,
)


def _main() -> int:
    passed: list[str] = []
    skipped: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []

    for check in _checks:
        name = check.__name__
        try:
            check()
        except _Skip as exc:
            skipped.append((name, str(exc)))
            print(f"SKIP  {name}\n      ⚠️ {exc}")
        except AssertionError as exc:
            failed.append((name, str(exc)))
            print(f"FAIL  {name}\n      {exc}")
        except Exception as exc:  # noqa: BLE001 - runner ko har error dikhana hai
            # 🔴 `AssertionError` se alag handle kiya hai kyunki ek TypeError ya
            #    KeyError "test ki expectation galat nikli" nahi hai — wo "test
            #    khud toot gaya" hai. Dono ko ek dikhana debugging mehnga karta.
            failed.append((name, f"{type(exc).__name__}: {exc}"))
            print(f"ERROR {name}\n      {type(exc).__name__}: {exc}")
        else:
            passed.append(name)
            print(f"ok    {name}")

    print()
    print(f"{len(passed)} pass · {len(skipped)} skip · {len(failed)} fail   (kul {len(_checks)})")

    if skipped:
        # Skip ko dabaya NAHI jaata. Ek line jo batati hai ki kitni checks ne
        # kuch dekha hi nahi, wo utni hi zaruri hai jitni fail wali line.
        print(f"⚠️ {len(skipped)} checks chali hi nahi — `make install` ke baad dobara chalao.")

    # 🔴 Agar EK BHI check na chali, exit code non-zero. Wajah: "0 fail" ka
    #    matlab "sab theek" nahi hota jab 0 checks chali hon. Ek green CI jisme
    #    kuch bhi verify nahi hua, wo red CI se zyada khatarnaak hai.
    if not passed:
        print("🔴 ek bhi check nahi chali — isko pass nahi maana ja sakta.")
        return 1
    if failed:
        # Baaki chaar files `raise SystemExit(...)` karti hain; yahan return
        # code hai kyunki `_main()` ko test se call karna hai (upar §6 dekho).
        return 1
    if skipped:
        # 🔴 "saare pass hue" NAHI likhte jab kuch chali hi nahi — wo technically
        #    sach hota (jo chali wo pass hui) aur practically ek jhooth.
        print(f"{len(passed)}/{len(_checks)} check pass hue; {len(skipped)} baaki hain (faker chahiye)")
        return 0
    print(f"saare {len(passed)} seed check pass hue (bina DB, bina pytest)")
    return 0


if __name__ == "__main__":
    print(f"tests/test_seed.py — scripts/seed_shopdb.py ke {len(_checks)} checks")
    print(f"faker: {'hai' if seed_shopdb._HAS_FAKER else 'NAHI (13 checks skip hongi)'}\n")
    sys.exit(_main())
