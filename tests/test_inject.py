"""test_inject.py — chaos ke inject/heal path ke tests.

🔴 Is file ka **sabse important** test wo nahi hai jo "inject kaam karta hai" check
karta hai. Wo `test_service_is_the_observation_point_not_the_culprit` hai.

Kyun: `incidents.service` column ka matlab hai **"symptom kahan dikha"**, na ki
**"kaun kharaab hai"**. Paanchon fault shopapp ke `/health` pe dikhte hain, lekin
F5 ka culprit shopcache hai aur F3/F4 ka shopdb. Agar kabhi kisi ne (aage chal ke,
"zyada informative" banane ke chakkar mein) `service` mein asli culprit likh diya,
toh agent — jo wahi row padhta hai — ko target **muft** mil jaayega, aur
"kya agent ne sahi localize kiya" ek tautology ban jaayega. Metric chup-chaap
jhoot bolne lagega, koi test red nahi hoga.

Isliye wo invariant yahan **do** tarah se pin hai: `chaos/inject.py` ke import-time
guard se, aur is file ke test se.

────────────────────────────────────────────────────────────────────────────
🔴 Ye tests bina psycopg, bina Docker, bina pytest chalte hain
────────────────────────────────────────────────────────────────────────────

    python3 tests/test_inject.py       # aaj, kisi bhi machine pe
    make test                          # pytest ke saath, jab venv ban jaaye

Dono naam se chalte hain kyunki har check ek `test_*` function hai (pytest inko
khud dhoond leta hai) aur file ke aakhir mein ek plain-Python runner bhi hai.
Yahi pattern `tests/test_broker.py` ka hai — jaan-boojh kar copy kiya, taaki
poore repo mein test chalane ka ek hi tareeka ho.

🔴 **Dry-run kuch nahi likhta — ye test us baat ko saabit karta hai, maanta nahi.**
`test_dry_run_never_opens_a_connection` `psycopg` ko ek booby-trap se replace kar
deta hai jo `connect()` pe phat jaata hai. Agar dry-run galti se DB chhue, test
crash karega. "psycopg installed nahi hai isliye safe hai" ek accident hai,
guarantee nahi — is machine pe sach hai, CI pe nahi.
"""

from __future__ import annotations

import ast
import contextlib
import io
import sys
from pathlib import Path

# 🔴 Repo root ko path pe daalo. Wajah: `python3 tests/test_inject.py` chalane pe
#    Python `tests/` ko sys.path[0] banata hai, repo root ko nahi — toh
#    `import chaos` fail ho jaata. pytest ye khud kar deta hai; hum dono
#    tareeke support kar rahe hain, toh ye line chahiye.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chaos import faults as F  # noqa: E402
from chaos import inject as I  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Chhote helpers
# ─────────────────────────────────────────────────────────────────────────────
def _run(argv: list[str]) -> tuple[int | str, str]:
    """`main(argv)` chalao, exit code aur poora output (stdout+stderr) do.

    Output capture karna zaroori hai kyunki kuch tests output ke **andar** dekhte
    hain (ground truth leak hui ya nahi). Aur `SystemExit` pakadte hain kyunki
    `_fault()` galat id pe wahi raise karta hai — test crash nahi hona chahiye.
    """
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code: int | str = I.main(argv)
        except SystemExit as exc:  # `_fault()` ka raise
            code = exc.code if exc.code is not None else 0
    return code, out.getvalue() + err.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# §1 — 🔴 Localization leak. Is file ka sabse important section.
# ─────────────────────────────────────────────────────────────────────────────
def test_service_is_the_observation_point_not_the_culprit() -> None:
    """`incidents.service` hamesha shopapp, chahe culprit koi bhi ho."""
    assert I.SERVICE_UNDER_OBSERVATION == "shopapp", (
        "Observation point badal gaya. Ye ek constant hai, per-fault value nahi — "
        "poori localization measurement isi pe khadi hai."
    )
    # 🔴 Asli baat: kam se kam ek fault ka culprit observation point se ALAG ho.
    #    Agar paanchon ka culprit shopapp hota, toh `service` = culprit hota aur
    #    localization measure karne ke liye kuch bacha hi nahi.
    culprits = {f.correct_target for f in F.FAULTS.values()}
    assert culprits != {I.SERVICE_UNDER_OBSERVATION}, (
        f"Saare culprits {culprits} = observation point. Localization ka "
        f"measurement meaningless ho gaya."
    )
    assert len(culprits) >= 2, f"Sirf {len(culprits)} distinct culprit: {culprits}"


def test_at_least_one_fault_lies_about_where_it_hurts() -> None:
    """Naam lekar wo faults pin karo jinka culprit shopapp NAHI hai.

    ⚠️ Ye test upar wale se **alag** hai. Upar wala set-level hai ("kam se kam do
    distinct culprit"). Ye per-fault hai: F3, F4, F5 ka culprit specifically
    shopapp se alag hona chahiye. Set-level check tab bhi pass ho jaayega jab
    sirf ek fault ka culprit shift ho; ye check batata hai **kaunsa**.
    """
    misleading = {
        fid: f.correct_target
        for fid, f in F.FAULTS.items()
        if f.correct_target != I.SERVICE_UNDER_OBSERVATION
    }
    assert misleading, "Koi bhi fault dhoka nahi de raha — localization trivial hai"
    for fid in ("F3", "F4", "F5"):
        assert fid in misleading, (
            f"{fid} ka culprit ab shopapp hai. Pehle nahi tha. "
            f"Ya fault redefine hua (DECISIONS.md mein likho), ya bug hai."
        )


def test_incident_row_always_says_shopapp() -> None:
    """Dry-run plan mein har fault ke liye `service='shopapp'` hi likha ho.

    Ye string-level check hai — kyunki wahi string SQL mein jaati hai. Ek din koi
    `_create_incident()` mein `fault.correct_target` likh de, toh ye test pakdega
    aur upar wale set-level check chup rehte (unko SQL nahi dikhti).
    """
    for fid in F.fault_ids():
        plan = I.inject(fid).plan
        joined = "\n".join(plan)
        assert "service='shopapp'" in joined, f"{fid}: plan mein service='shopapp' nahi"
        target = F.FAULTS[fid].correct_target
        if target != I.SERVICE_UNDER_OBSERVATION:
            assert f"service='{target}'" not in joined, (
                f"🔴 {fid}: plan mein culprit '{target}' service ke roop mein leak hua"
            )


# ─────────────────────────────────────────────────────────────────────────────
# §2 — 🔴 Dry-run kuch nahi karta. Ye saabit karna hai, maanna nahi.
# ─────────────────────────────────────────────────────────────────────────────
class _BoobyTrappedPsycopg:
    """`psycopg` ka naqli version jo chhune se hi phat jaata hai.

    Kyun class aur `None` nahi: `None` set karne se code `psycopg is None` branch
    le lega aur ek dost jaisa error message dega — yaani test ko *pata hi nahi
    chalega* ki DB path chala. Ek exploding stub hi asli baat batata hai:
    connection open karne ki **koshish** hui ya nahi.
    """

    class _Boom(Exception):
        pass

    @staticmethod
    def connect(*_a: object, **_k: object) -> None:
        raise _BoobyTrappedPsycopg._Boom(
            "🔴 Dry-run ne DB connection kholne ki koshish ki. Ye CLAUDE.md §3 ka "
            "violation hai: '--write ke bina kuch nahi'."
        )


def test_dry_run_never_opens_a_connection() -> None:
    original = I.psycopg
    I.psycopg = _BoobyTrappedPsycopg  # type: ignore[assignment]
    try:
        for fid in F.fault_ids():
            result = I.inject(fid)  # write=False default
            assert result.written is False, f"{fid}: dry-run ne written=True diya"
            assert result.break_output == "", f"{fid}: dry-run ne container chhua"
    finally:
        # 🔴 `finally` zaroori hai. Test fail hone pe bhi module wapas normal ho,
        #    warna baad ke saare tests booby-trapped psycopg pe chalenge aur
        #    unka failure jhoot hoga.
        I.psycopg = original  # type: ignore[assignment]


def test_incident_id_is_none_not_zero() -> None:
    """Dry-run ka `incident_id` `None` ho, `0` nahi.

    🔴 `0` ek asli id jaisa dikhta hai. Aur `if incident_id:` (jo har koi likhta
    hai) `0` ko chup-chaap "koi id nahi" pad leta hai — yaani `0` galti se sahi
    kaam kar deta hai, jab tak kisi din nahi karta. `None` kehta hai "row bani hi
    nahi", aur `int` ke saath compare karne pe crash karta hai, chup nahi rehta.
    """
    result = I.inject("F1")
    assert result.incident_id is None
    assert result.incident_id is not False, "bool bhi nahi — sirf None"


def test_the_plan_is_data_not_a_print() -> None:
    """`build_plan()` string tuple return karta hai, print nahi karta.

    Kyun ye test hai: agar plan seedha `print()` hota, toh (a) test usko padh nahi
    sakta, aur (b) aage chal ke `POST /api/faults/{id}/inject` uska JSON response
    nahi bana sakta. Plan **data** hai — CLI usko print karti hai, banati nahi.
    """
    plan = I.build_plan(F.FAULTS["F4"], with_incident=True, dsn="postgresql://x@y/z")
    assert isinstance(plan, tuple), f"plan {type(plan).__name__} hai, tuple nahi"
    assert all(isinstance(line, str) for line in plan)
    assert len(plan) >= 5, f"plan mein sirf {len(plan)} line — kuch gayab hai"


def test_no_incident_flag_removes_the_db_lines() -> None:
    with_rows = "\n".join(I.build_plan(F.FAULTS["F1"], with_incident=True, dsn="postgresql://a@b/c"))
    without = "\n".join(I.build_plan(F.FAULTS["F1"], with_incident=False, dsn="postgresql://a@b/c"))
    assert "incidents  +1 row" in with_rows
    assert "incidents  +1 row" not in without, "--no-incident ne DB line nahi hataayi"
    assert "--no-incident" in without, "--no-incident ka reason plan mein likha ho"


# ─────────────────────────────────────────────────────────────────────────────
# §3 — 🔒 Ground truth leak. `--list` bina `--spoil` ke jawaab nahi deta.
# ─────────────────────────────────────────────────────────────────────────────
#: 🔴 Ye wo teen field hain jo agent ko kabhi nahi dikhne chahiye. `CLAUDE.md` §3:
#:   "nothing under `oncall/` may import from `chaos/` or read `correct_action` /
#:   `is_relevant`". Terminal output us rule ka teesra raasta hai — scrollback
#:   copy-paste ho kar prompt mein pahunch sakta hai.
_SECRET_FIELDS = ("correct_action", "correct_target", "relevant_runbooks", "also_acceptable")


def test_list_without_spoil_leaks_nothing() -> None:
    text = I.list_faults()
    for field in _SECRET_FIELDS:
        assert field not in text, f"🔴 --list ne '{field}' ka naam leak kiya"
    # Field ka naam chhupana kaafi nahi — uski **value** bhi nahi dikhni chahiye.
    for fid, f in F.FAULTS.items():
        assert f.correct_action not in text, (
            f"🔴 --list ne {fid} ka answer '{f.correct_action}' leak kiya"
        )
    assert "--spoil" in text, "Hint hona chahiye ki dekhne ka tareeka kya hai"


def test_spoil_actually_shows_the_answers() -> None:
    """Negative control: agar `--spoil` bhi kuch na dikhaye, toh upar wala test
    kuch saabit nahi karta — wo ek khaali string pe bhi pass ho jaata."""
    text = I.list_faults(spoil=True)
    for fid, f in F.FAULTS.items():
        assert f.correct_action in text, f"--spoil ne {fid} ka answer nahi dikhaya"
        assert f.correct_target in text, f"--spoil ne {fid} ka target nahi dikhaya"


def test_dry_run_plan_leaks_nothing_either() -> None:
    """Plan bhi output hai. Wo bhi answer nahi bol sakta.

    ⚠️ Yahan `correct_target` ki **value** allow karni padegi kya? Nahi. F4 ka
    plan `oncall-shopdb` chhapta hai (`touches`), aur wo container naam hai —
    `correct_target` short naam (`shopdb`) hai. Do vocabularies alag hone ka ek
    accidental fayda: container list target ko reveal nahi karti.
    """
    for fid, f in F.FAULTS.items():
        joined = "\n".join(I.inject(fid).plan)
        assert f.correct_action not in joined, f"🔴 {fid} ka plan answer bol raha hai"
        for field in _SECRET_FIELDS:
            assert field not in joined, f"🔴 {fid} ka plan '{field}' bol raha hai"


# ─────────────────────────────────────────────────────────────────────────────
# §4 — Credential hygiene. `_redact_dsn()`.
# ─────────────────────────────────────────────────────────────────────────────
def test_redact_dsn_hides_the_password() -> None:
    """🔴 Ye bug asli tha. Pehla dry-run poora DSN chhapta tha, password ke saath —
    aur wo output demo recording, terminal scrollback, aur GitHub issue mein
    kahin bhi ja sakta tha."""
    cases = [
        ("postgresql://oncall:oncall@127.0.0.1:5433/oncall", "oncall:***@"),
        ("postgresql://oncall:s3cr3t:with:colons@host/db", "oncall:***@"),
    ]
    for dsn, expected in cases:
        out = I._redact_dsn(dsn)
        assert expected in out, f"{dsn!r} → {out!r}, '{expected}' expected tha"
        assert "s3cr3t" not in out and "oncall:oncall" not in out, f"password bacha: {out!r}"


def test_redact_dsn_survives_garbage() -> None:
    """Malformed DSN pe crash nahi hona chahiye.

    🔴 Wajah: ye function ek **dry-run printer** mein chalta hai. Dry-run ka poora
    point ye hai ki wo safe ho. Ek galat `ONCALL_DB_URL` par crash karne wala
    redactor dry-run ko hi tod dega — yaani safety check khud unsafe ho jaayega.
    Isliye ye `urllib.parse` use nahi karta; wo strict hai, hum forgiving chahte hain.
    """
    for junk in ("", "not-a-dsn", "postgresql://", "postgresql://nouser@host/db", "://@@@"):
        out = I._redact_dsn(junk)
        assert isinstance(out, str), f"{junk!r} pe string nahi mili"


def test_the_real_dsn_gets_redacted_in_the_plan() -> None:
    """End-to-end: settings ka asli DSN plan mein masked jaaye."""
    joined = "\n".join(I.inject("F1").plan)
    assert "***" in joined, "plan mein masked DSN nahi dikha"
    assert "oncall:oncall@" not in joined, "🔴 asli password plan mein leak hua"


# ─────────────────────────────────────────────────────────────────────────────
# §5 — `Fault.touches` — dry-run ka wo waada jo data se pura hota hai
# ─────────────────────────────────────────────────────────────────────────────
def test_touches_is_explicit_data_not_derived() -> None:
    """F4 ka target shopdb hai lekin uska heal shopapp ko bhi band karta hai.

    🔴 Yahi wajah hai ki `touches` ek alag field hai. Agar dry-run `correct_target`
    se container list banata, toh wo F4 pe **jhoot** bolta: "sirf shopdb chhuunga",
    aur phir heal shopapp ko band kar deta. Ek dry-run jo galat batata hai, no
    dry-run se bura hai.
    """
    f4 = F.FAULTS["F4"]
    assert f4.correct_target == "shopdb"
    assert F.SHOPAPP in f4.touches, (
        "F4 ke touches se shopapp gayab. `_heal_f4` shopapp ko band karta hai "
        "(taaki heal usi 'too many clients' deewar se na takraye jo wo theek kar "
        "raha hai). Wo baat `touches` mein likhi honi chahiye."
    )
    assert len(f4.touches) > 1, "F4 ek se zyada container chhuta hai"


def test_every_touched_name_is_a_real_container() -> None:
    for fid, f in F.FAULTS.items():
        assert f.touches, f"{fid}: touches khaali — dry-run kya print karega?"
        for name in f.touches:
            assert name in F._KNOWN_CONTAINERS, f"{fid}: '{name}' koi asli container nahi"


def test_chaos_may_not_touch_the_agents_own_database() -> None:
    """🔴 `oncall-db` jaan-boojh kar `_KNOWN_CONTAINERS` mein nahi hai.

    Chaos ka kaam **victim stack** todna hai (shopapp/shopdb/shopcache). `oncall-db`
    agent ka apna audit trail hai — usme incidents, events, jobs, llm_calls padi
    hain. Usko fault ke daayre mein laana matlab apna hi evidence uda dena, aur
    phir "kya hua tha" ka jawaab kabhi nahi milega.
    """
    assert F.ONCALL_DB not in F._KNOWN_CONTAINERS
    for fid, f in F.FAULTS.items():
        assert F.ONCALL_DB not in f.touches, f"🔴 {fid} agent ke apne DB ko chhu raha hai"


# ─────────────────────────────────────────────────────────────────────────────
# §6 — CLI contract. Exit code ek API hai.
# ─────────────────────────────────────────────────────────────────────────────
#: 🔴 Exit codes jaan-boojh kar teen hain:
#:     0 = kaam ho gaya (ya dry-run theek chala)
#:     1 = jo maanga wo exist nahi karta / runtime failure
#:     2 = usage galat hai — command hi banti nahi
#:   Kyun farq: `make heal F=F9` (typo) aur `make heal F=F4` (DB down) do alag
#:   cheezein hain, aur CI/script ko farq pata hona chahiye.
_CLI_CASES = (
    (["--list"], 0, "--list akela"),
    (["--list", "--spoil"], 0, "--list --spoil"),
    (["--list", "F4"], 2, "--list + fault → usage error"),
    (["F4", "--heal-all"], 2, "fault + --heal-all → contradiction"),
    (["--heal-all", "--heal"], 2, "--heal-all + --heal → contradiction"),
    (["--heal-all"], 0, "--heal-all akela (dry-run)"),
    (["F4"], 0, "inject dry-run"),
    (["F5", "--heal"], 0, "heal dry-run"),
    ([], 2, "kuch bhi nahi diya"),
    (["F9"], 1, "aisi fault id nahi hai"),
    (["F4", "--no-incident"], 0, "--no-incident dry-run"),
    (["F4", "--no-rag"], 0, "--no-rag dry-run"),
    (["f4"], 0, "lowercase id chalti hai"),
    (["  F4  "], 0, "extra spaces trim hoti hain"),
)


def test_cli_exit_codes() -> None:
    for argv, want, label in _CLI_CASES:
        code, _ = _run(list(argv))
        assert code == want, f"{label}: `{' '.join(argv) or '(khaali)'}` → {code}, {want} chahiye tha"


def test_heal_all_with_a_fault_id_is_refused_not_silently_ignored() -> None:
    """🔴 Ye ek asli bug tha jo test likhte waqt pakda gaya.

    Pehla `main()` `--heal-all` ko jeeta deta tha aur `F4` chup-chaap ignore kar
    deta tha. Yaani `make heal-all F=F4` chalane wala aadmi 2 container expect
    karta aur 3 chhue jaate — `--write` ke saath. Ek galat kaam chup-chaap karne
    se acha hai mana kar dena.
    """
    code, out = _run(["F4", "--heal-all"])
    assert code == 2
    assert "--heal-all akela chalta hai" in out, f"wajah nahi batayi: {out!r}"


def test_bad_fault_id_says_what_is_available() -> None:
    """Error message mein raasta hona chahiye, sirf "nahi mila" nahi."""
    code, out = _run(["F9"])
    assert code == 1
    for fid in F.fault_ids():
        assert fid in out, f"error message mein {fid} nahi tha"


def test_dry_run_output_says_it_did_nothing() -> None:
    """Sabse mehnga wala confusion: aadmi dry-run chala kar samjhe ki kaam ho gaya."""
    _, out = _run(["F4"])
    assert "DRY-RUN" in out
    assert "--write" in out, "output mein wo flag hona chahiye jo asli kaam karta hai"


def test_help_examples_are_real_commands() -> None:
    """🔴 Epilog ke examples ko parse karke dekho ki wo aaj bhi valid hain.

    Kyun: `--help` ka epilog documentation hai, aur `CLAUDE.md` §2 kehta hai
    "run a verification command before writing it into a doc as proof". Ek din
    koi flag rename karega aur epilog jhoot bolne lagega. Ye test us din red hoga.
    """
    parser = I._build_parser()
    epilog = parser.epilog or ""
    found = 0
    for raw in epilog.splitlines():
        line = raw.strip()
        if not line.startswith("python -m chaos.inject"):
            continue
        found += 1
        # Comment hata do, phir module ke baad ke args parse karao.
        cmd = line.split("#", 1)[0].strip()
        argv = cmd.split()[3:]
        with contextlib.redirect_stderr(io.StringIO()):
            parser.parse_args(argv)  # invalid flag pe SystemExit → test fail
    assert found >= 5, f"epilog mein sirf {found} example — kuch gayab hai"


# ─────────────────────────────────────────────────────────────────────────────
# §7 — 🔴 Negative controls. Guards ko todo aur dekho ki wo chille.
#
# Ek guard jo kabhi fire hote nahi dekha gaya, wo guard hai ya comment — pata nahi.
# ⚠️ Ye sabak mehnga tha: pehli koshish mein maine ek 6th fault ADD kiya, jisse
#    `_check_registry()` ka pehla check (`len(FAULTS) != 5`) trip ho gaya aur wo
#    container check chala hi nahi — test green, guard untested. Isliye ab har
#    negative control ek fault ko **replace** karta hai, add nahi karta.
# ─────────────────────────────────────────────────────────────────────────────
def _planted(fid: str, **overrides: object) -> dict[str, F.Fault]:
    """`FAULTS` ki copy jisme ek fault badla hua hai. Count wahi 5 rehta hai."""
    import dataclasses

    patched = dict(F.FAULTS)
    patched[fid] = dataclasses.replace(F.FAULTS[fid], **overrides)  # type: ignore[arg-type]
    return patched


def _expect_registry_raise(patched: dict[str, F.Fault], must_say: str, label: str) -> None:
    original = F.FAULTS
    F.FAULTS = patched  # type: ignore[assignment]
    try:
        try:
            F._check_registry()
        except RuntimeError as exc:
            assert must_say in str(exc), f"{label}: raise hua par galat wajah se — {exc}"
        else:
            raise AssertionError(f"🔴 {label}: guard chup raha. Wo guard nahi hai.")
    finally:
        F.FAULTS = original  # type: ignore[assignment]


def test_negative_control_empty_touches_is_caught() -> None:
    _expect_registry_raise(_planted("F5", touches=()), "touches khaali", "khaali touches")


def test_negative_control_fake_container_is_caught() -> None:
    _expect_registry_raise(
        _planted("F5", touches=("oncall-nonexistent",)), "koi asli container nahi", "farzi container"
    )


def test_negative_control_agents_own_db_is_caught() -> None:
    _expect_registry_raise(
        _planted("F5", touches=(F.ONCALL_DB,)), "koi asli container nahi", "oncall-db in touches"
    )


def test_negative_control_localization_leak_is_caught() -> None:
    """🔴 Sabse important negative control: agar SAARE culprits shopapp ho jaayein,
    toh `chaos/inject.py` ka import-time guard chillana chahiye.

    🔴 **`I.FAULTS` bhi patch karna PADTA hai — aur ye is test ka asli sabak hai.**

    `chaos/inject.py` ki pehli lines mein hai:

        from chaos.faults import FAULTS

    `from X import Y` ek *copy of the reference* banata hai — `chaos.inject` ke
    apne module namespace mein `FAULTS` naam ka apna binding. Toh sirf
    `F.FAULTS = ...` set karne se `chaos.faults` ka naam badalta hai, lekin
    `chaos.inject` ka naam **purane dict pe hi** ishara karta rehta hai. Guard
    (`I._check_no_localization_leak`) apne module ka naam padhta hai, toh usne
    asli, saaf-suthri list dekhi aur — sahi hi — chup raha.

    Pehla version yahi galti karta tha: test fail hua aur maine socha guard toota
    hai. Guard theek tha; test jhoothi jagah pe zor laga raha tha. Ek negative
    control jo galat module patch karta hai, wo kuch prove nahi karta —
    isliye ye comment itna lamba hai.

    (Agar `inject.py` ne `from chaos import faults` kiya hota aur `faults.FAULTS`
     likha hota, toh ek patch kaafi hota. Wo bhi ek valid style hai; hum
     `from X import Y` rakh rahe hain kyunki wo padhne mein saaf hai — bas
     monkeypatching ki keemat pata honi chahiye.)
    """
    import dataclasses

    leaked = {
        fid: dataclasses.replace(f, correct_target=I.SERVICE_UNDER_OBSERVATION)
        for fid, f in F.FAULTS.items()
    }
    original_f, original_i = F.FAULTS, I.FAULTS
    F.FAULTS = leaked  # type: ignore[assignment]
    I.FAULTS = leaked  # type: ignore[assignment]
    try:
        try:
            I._check_no_localization_leak()
        except RuntimeError as exc:
            assert "shopapp" in str(exc) or "localiz" in str(exc).lower(), (
                f"raise hua par message se wajah samajh nahi aayi: {exc}"
            )
        else:
            raise AssertionError(
                "🔴 Saare culprits shopapp kar diye aur guard chup raha. "
                "Yaani localization measurement bina awaaz ke tautology ban sakti hai."
            )
    finally:
        F.FAULTS = original_f  # type: ignore[assignment]
        I.FAULTS = original_i  # type: ignore[assignment]


def test_the_guards_use_raise_not_assert() -> None:
    """🔴 `python -O` `assert` ko strip kar deta hai.

    Ek guard jo production flag ke saath gayab ho jaaye, wo guard nahi hai. Isliye
    import-time checks `raise RuntimeError(...)` hain. Ye test source padh kar
    check karta hai — behaviour se nahi, kyunki `-O` is process pe on nahi hai.
    """
    for path, func in ((F.__file__, "_check_registry"), (I.__file__, "_check_no_localization_leak")):
        src = Path(str(path)).read_text(encoding="utf-8")
        start = src.index(f"def {func}(")
        # Function ka body agla top-level `def`/statement tak. Kaafi hai — checks
        # function ke shuru mein hi hain.
        body = src[start : src.index("\n\n\n", start)]
        assert "raise " in body, f"{func} mein koi raise hi nahi"
        assert "    assert " not in body, f"🔴 {func} `assert` use kar raha hai — -O usko uda dega"


# ─────────────────────────────────────────────────────────────────────────────
# §8 — 🔴 heal() koi DB row nahi likhta. Ye ek metrics invariant hai.
# ─────────────────────────────────────────────────────────────────────────────
def test_heal_writes_no_database_row() -> None:
    """`heal()` operator ka reset button hai, agent ka fix nahi.

    🔴 Agar heal `state='RECOVERED'` likhta, toh mere haath se kiye reset aur
    agent ke asli fix metrics mein **ek jaise** dikhte, aur N1 (per-fault auto-fix
    rate) mere apne hand-fixes ginne lagta. Verdict sirf `oncall/oracle/health.py`
    likhta hai — `CLAUDE.md` §3.

    Test kaise: `heal()` ka source padho aur dekho ki wahan koi INSERT/UPDATE nahi.
    Behaviour se test nahi kar sakte kyunki heal ko Docker chahiye.
    """
    src = Path(str(I.__file__)).read_text(encoding="utf-8")
    for func in ("def heal(", "def heal_all("):
        start = src.index(func)
        body = src[start : src.index("\n\n\n", start)]
        for sql in ("INSERT", "UPDATE", "psycopg", "connect("):
            assert sql not in body, f"🔴 {func.strip('def (')} mein '{sql}' hai — heal DB chhu raha hai"


def test_heal_tells_you_the_dry_run_was_a_dry_run() -> None:
    out = I.heal("F4")
    assert "dry-run" in out.lower()
    assert "--write" in out
    for name in F.FAULTS["F4"].touches:
        assert name in out, f"heal ka dry-run '{name}' nahi bata raha"


def test_heal_all_covers_every_fault() -> None:
    lines = I.heal_all()
    assert len(lines) == len(F.FAULTS), f"{len(lines)} heal, {len(F.FAULTS)} fault"
    joined = "\n".join(lines)
    for fid in F.fault_ids():
        assert fid in joined, f"heal_all ne {fid} chhoda"


# ─────────────────────────────────────────────────────────────────────────────
# §9 — 🔴 Import direction. chaos → oncall theek hai; oncall → chaos banned.
# ─────────────────────────────────────────────────────────────────────────────
def test_import_barrier_is_one_directional() -> None:
    """`chaos/inject.py` `oncall.config` import karta hai — aur wo **allowed** hai.

    Barrier jaan-boojh kar asymmetric hai:
      chaos → oncall  ✅  chaos ko DSN chahiye; ek hi default ho, do nahi
      oncall → chaos  ❌  warna agent ko `correct_action` mil jaayega

    Ye test dono side pin karta hai. Sirf "oncall chaos import nahi karta" check
    karne wala test tab bhi green rehta jab chaos ne apna DSN default duplicate
    kar liya ho — aur phir do defaults drift karte, chup-chaap.
    """
    src = Path(str(I.__file__)).read_text(encoding="utf-8")
    assert "from oncall.config import settings" in src, (
        "chaos ne apna DSN default banaya? Ek hi source of truth hona chahiye."
    )
    repo = Path(str(I.__file__)).resolve().parent.parent
    offenders = []
    for py in sorted((repo / "oncall").rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith(("import chaos", "from chaos")):
                offenders.append(f"{py.relative_to(repo)}: {stripped}")
    assert not offenders, "🔴 oncall/ ne chaos/ import kiya:\n  " + "\n  ".join(offenders)


#: 🔴 Answer-key tokens ki **authoritative list** `Makefile` ke `lint` target
#:   (check 4) mein hai, aur `CLAUDE.md` §3 mein. Ye tuple uska teesra copy
#:   nahi hai — `test_token_list_matches_makefile` Makefile ko padh kar compare
#:   karta hai. Ek rule ke do enforcer agar do list rakhein, wo drift hai.
_BANNED_TOKENS = (*_SECRET_FIELDS, "is_relevant")


def _answer_key_hits(src: str, filename: str = "<planted>") -> list[str]:
    """`oncall/` mein answer-key token ka **koi bhi** zikr — code ho ya prose.

    🔥 Is check ka pehla version line-based tha aur `#` comment ko chhod deta
       tha. Wo `oncall/db/queries.py` ke ek **docstring** line pe fail hua — us
       line pe jo khud rule likh rahi thi. Do raaste the:

       (a) AST likho, taaki docstring "mention" bane aur code "use".
       (b) Prose reword karo, taaki forbidden token source mein hi na ho.

       AST technically behtar hai aur `SELECT *` ke liye humne wahi chuna
       (`tests/test_schema.py`). **Yahan (b) chuna gaya**, kyunki is project ne
       pehle se, likhit roop mein, ulta faisla kar rakha hai: `Makefile` ka
       `lint` comment kehta hai *"Koi exception mechanism NAHI hai — na
       `# noqa`, na `# lint-ok`"*, aur `oncall/__init__.py` ka docstring wo
       keemat already chuka raha hai (*"Ye docstring un forbidden tokens ko
       likhta NAHI hai, sirf describe karta hai"*).

    🔴 Asli lesson isliye ye hai: *ek rule ke do enforcer alag nahi ho sakte.*
       Agar ye test AST hota, toh `make test` green aur `make lint` red hota —
       aur us haalat mein aadmi wahi enforcer maanta hai jo usko pass karta
       hai. Toh ye scan bhi **bilkul** grep jaisa hai: koi exemption nahi, na
       comment ka, na docstring ka.

    Keemat jo maan li gayi: `oncall/` ke andar rule ko *describe* karna padta
    hai, uske field ka naam le kar nahi. Documentation ke liye mehenga hai.
    Fayda: audit ek grep hai jise koi comment chup nahi kara sakta.
    """
    hits: list[str] = []
    for n, line in enumerate(src.splitlines(), 1):
        for token in _BANNED_TOKENS:
            if token in line:
                hits.append(f"{filename}:{n}: {token}")
    return hits


def test_oncall_never_reads_the_answer_key() -> None:
    """🔴 `CLAUDE.md` §3 — `oncall/` mein answer-key token ka naam bhi nahi.

    Import barrier (`test_import_barrier_is_one_directional`) `chaos/` ko rokta
    hai, lekin ground truth doosre raaste se bhi aa sakti hai: koi `runbooks`
    se answer-key column SELECT kar le, ya kahin ek dict mein fault ka
    expected-action field daal de. Ye check un raaston ko band karta hai — aur
    prose ko bhi, jaanbujh ke (`_answer_key_hits` ka docstring dekho).

    Ye test `make lint` check 4 ka **exact** duplicate hai, aur wo jaanbujh ke
    hai: ek boundary ko `make test` aur `make lint` dono se pakadna chahiye,
    kyunki dono alag-alag mauke pe chalte hain.
    """
    repo = Path(str(I.__file__)).resolve().parent.parent
    offenders: list[str] = []
    scanned = 0
    for py in sorted((repo / "oncall").rglob("*.py")):
        scanned += 1
        offenders += _answer_key_hits(
            py.read_text(encoding="utf-8"), filename=str(py.relative_to(repo))
        )
    # 🔥 Scan chala hi — warna "0 offender" ka matlab "kuch dekha nahi" hai.
    assert scanned >= 15, f"sirf {scanned} file scan hui `oncall/` mein"
    assert not offenders, (
        "🔴 oncall/ mein answer-key token mila (comment/docstring ko bhi "
        "exemption nahi hai — rule ko describe karo, naam mat lo):\n  "
        + "\n  ".join(offenders)
    )


def test_negative_control_answer_key_check_can_fail() -> None:
    """🔥 Dono taraf se zid — warna is check ka green matlab-less hai.

    Planted source mein 7 hit hone chahiye, aur unme se **do** jaanbujh ke
    "sirf zikr" hain — ek module docstring, ek `#` comment. Wo bhi count hote
    hain: yahi is check ka poora point hai. Agar kal koi isko "smart" bana kar
    comment/docstring exempt kar de, ye test 7 ki jagah 5 dega aur red ho
    jaayega — aur tab wo `make lint` se bhi disagree kar raha hoga.

    ⚠️ Aakhri line pe teen token ek saath hain → teen hit, ek nahi. Ek line pe
       teen problem ka matlab teen jagah hain jinko insaan ko dekhna padega.
    """
    planted = "\n".join(
        [
            '"""Rule: `correct_action` banned hai — ye docstring bhi count hoti hai."""',
            "# is_relevant ek column hai — comment ko bhi exemption nahi",
            "def probe(fault):",
            "    a = fault.correct_action",
            "    correct_target = 1",
            "    return a, correct_target, relevant_runbooks, also_acceptable",
        ]
    )
    found = _answer_key_hits(planted)
    assert len(found) == 7, (
        f"negative control fail: 7 hit expected, mili {len(found)} → {found}. "
        "Matlab kisi ne scan mein exemption daal di hai."
    )


def test_token_list_matches_makefile() -> None:
    """🔴 Anti-drift: is file ki token list == `Makefile` check 4 ki list.

    Pehle Makefile 2 token grep karta tha (`correct_action|is_relevant`) aur ye
    test 4. Ek hi rule, do definitions — aur us gap mein
    `oncall/actions/allowlist.py` ki ek line mahino baithi rahi. Isliye ab
    dono ek doosre se bandhe hain: list badalni hai toh Makefile mein badlo,
    warna ye test red.
    """
    mk = (Path(str(I.__file__)).resolve().parent.parent / "Makefile").read_text(
        encoding="utf-8"
    )
    # Check 4 ka pattern ek single-quoted alternation hai. Usko dhoondhne ka
    # sabse seedha tareeka: wahi line jisme hamare token ka `|` alternation ho.
    pattern = ""
    for line in mk.splitlines():
        # Makefile ki continuation line `\` pe khatam hoti hai — usko pehle
        # hatao, warna `endswith("'")` kabhi match nahi karega. (Ye galti pehli
        # baar mein hui thi: test red hua "pattern nahi mila" keh kar.)
        s = line.strip().rstrip("\\").strip()
        if s.startswith("'") and s.endswith("'") and "correct_action" in s:
            pattern = s.strip("'")
            break
    assert pattern, "Makefile mein check 4 ka token pattern nahi mila"
    assert set(pattern.split("|")) == set(_BANNED_TOKENS), (
        f"drift: Makefile → {sorted(pattern.split('|'))}, "
        f"test → {sorted(_BANNED_TOKENS)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# §10 — Runner ka apna negative control
#
# 🔴 Ek test jo `_checks` mein nahi hai, wo **kabhi chalta hi nahi** — aur file
#    padhne wale ko lagta hai ki chal raha hai. `test_broker.py` mein bhi yahi
#    guard hai. Ye AST se file padh kar har `def test_*` dhoondhta hai aur `_checks`
#    se milaata hai.
# ─────────────────────────────────────────────────────────────────────────────
def test_runner_lists_every_test_in_this_file() -> None:
    import ast

    src = Path(__file__).read_text(encoding="utf-8")
    in_file = {
        node.name
        for node in ast.parse(src).body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }
    listed = {c.__name__ for c in _checks}
    missing = in_file - listed
    assert not missing, f"🔴 Ye test _checks mein nahi hain, yaani chalte hi nahi: {sorted(missing)}"


_checks = (
    # §1 — localization leak (is file ka core)
    test_service_is_the_observation_point_not_the_culprit,
    test_at_least_one_fault_lies_about_where_it_hurts,
    test_incident_row_always_says_shopapp,
    # §2 — dry-run kuch nahi karta
    test_dry_run_never_opens_a_connection,
    test_incident_id_is_none_not_zero,
    test_the_plan_is_data_not_a_print,
    test_no_incident_flag_removes_the_db_lines,
    # §3 — ground truth leak
    test_list_without_spoil_leaks_nothing,
    test_spoil_actually_shows_the_answers,
    test_dry_run_plan_leaks_nothing_either,
    # §4 — credential hygiene
    test_redact_dsn_hides_the_password,
    test_redact_dsn_survives_garbage,
    test_the_real_dsn_gets_redacted_in_the_plan,
    # §5 — Fault.touches
    test_touches_is_explicit_data_not_derived,
    test_every_touched_name_is_a_real_container,
    test_chaos_may_not_touch_the_agents_own_database,
    # §6 — CLI contract
    test_cli_exit_codes,
    test_heal_all_with_a_fault_id_is_refused_not_silently_ignored,
    test_bad_fault_id_says_what_is_available,
    test_dry_run_output_says_it_did_nothing,
    test_help_examples_are_real_commands,
    # §7 — negative controls
    test_negative_control_empty_touches_is_caught,
    test_negative_control_fake_container_is_caught,
    test_negative_control_agents_own_db_is_caught,
    test_negative_control_localization_leak_is_caught,
    test_the_guards_use_raise_not_assert,
    # §8 — heal writes nothing
    test_heal_writes_no_database_row,
    test_heal_tells_you_the_dry_run_was_a_dry_run,
    test_heal_all_covers_every_fault,
    # §9 — import barrier
    test_import_barrier_is_one_directional,
    test_oncall_never_reads_the_answer_key,
    test_negative_control_answer_key_check_can_fail,
    test_token_list_matches_makefile,
    # §10 — runner ka control
    test_runner_lists_every_test_in_this_file,
)


if __name__ == "__main__":
    _failed = 0
    print(f"{len(F.FAULTS)} fault classes · {len(_checks)} checks\n")
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
    print(f"saare {len(_checks)} inject check pass hue")















