"""Structural guardrails — AST se, grep se nahi.

Ye file teen hard constraints ko machine-checkable banati hai. Teeno CLAUDE.md
§3 mein likhe hain, aur teeno ke liye grep **kaafi nahi** hai:

    1. 🔒 `oncall/` mein shell nahi           (subprocess / os.system / popen / shell=True)
    2. 🔴 `oncall/` `chaos/` se import nahi karta   (ground truth invisible)
    3. 🔒 LLM verdict decide nahi karta       (koi "judge" naam nahi)

────────────────────────────────────────────────────────────────────────────
Grep kyun kaafi nahi (CLAUDE.md §1 — concept pehle)
────────────────────────────────────────────────────────────────────────────

Grep text dekhta hai. Usko nahi pata ki `subprocess` ek import hai ya ek
comment mein likha hua shabd. Aur is repo mein wo shabd comments mein
**jaan-boojh kar** likhe hain — kyunki hum samjha rahe hain ki wo kyun banned
hain. Toh naive grep hamesha match karega, aur ek check jo hamesha fail hota
hai, wo ignore kiya jaane lagta hai. Phir asli violation bhi chhup jaata hai.

AST = Abstract Syntax Tree. Python khud source ko parse karke ek tree banata
hai — usme `import`, function call, string, sab **alag node types** hain. Aur
comments us tree mein **hote hi nahi** (parser unhe phenk deta hai). Toh AST
walk sirf asli code dekhta hai.

Rejected alternative: `grep -v '^\\s*#'` se comments hata do. Wo multi-line
docstrings pe toot jaata (wo comment nahi, string hai), aur ek `#` andar-string
wale case pe bhi. Aadha-sahi filter sabse khatarnaak hai — wo bharosa deta hai
jo wo kama nahi sakta.

Chalane ke do tareeke (pytest install na ho toh bhi chalta hai):

    python3 -m pytest tests/test_no_shell.py -q
    python3 tests/test_no_shell.py
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ONCALL = REPO_ROOT / "oncall"


# ─────────────────────────────────────────────────────────────────────────────
# Kaunsi files check karni hain
# ─────────────────────────────────────────────────────────────────────────────


def _python_files() -> list[Path]:
    """`oncall/` ke neeche saari asli `.py` files.

    🔴 `__pycache__` chhodna **zaroori** hai, aur wajah dilchasp hai: `.pyc`
       compiled bytecode hai, aur usme banned shabd literal strings ke roop
       mein maujood hote hain (docstrings compile ho kar usme chali jaati
       hain). Ek grep `.pyc` pe match kar jaata hai — pichhle segment mein
       exactly yahi hua. AST walk `.py` source hi padhta hai, toh ye filter
       bas safai ke liye hai, bachaav ke liye nahi.

    ⚠️ Agar ye list khaali aayi, toh test **fail** hota hai (neeche
       `test_files_were_actually_scanned`). Ek guard jo 0 files scan kar ke
       "pass" bol de, wo guard nahi — wo jhoothi tasalli hai.
    """
    return sorted(p for p in ONCALL.rglob("*.py") if "__pycache__" not in p.parts)


def _parse(path: Path) -> ast.Module:
    """Ek file ko AST mein badlo.

    `SyntaxError` ko yahan pakadna jaan-boojh kar nahi kiya — agar `oncall/`
    ki koi file parse hi nahi hoti, toh wo apne aap mein ek failure hai jo
    is test ko lal karna chahiye, chhup kar skip nahi hona chahiye.
    """
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# ─────────────────────────────────────────────────────────────────────────────
# 🔒 Check 1 — `oncall/` mein shell nahi (CLAUDE.md §3)
# ─────────────────────────────────────────────────────────────────────────────
#
# Concept pehle (CLAUDE.md §1): "shell" ek program hai (`/bin/sh`) jo ek
# **string** leta hai aur usme `;`, `&&`, `|`, `$(...)`, `*` ko *meaning* deta
# hai. Hum Docker SDK ko list bhejte hain — `["find", path, "-mindepth", "1",
# "-delete"]` — toh koi shell involve hi nahi hoti, aur us list ka koi element
# "do commands" nahi ban sakta. LLM ka output attacker-controlled hai (prompt
# injection container logs ke through aa sakti hai), isliye ye structural hai,
# escaping-based nahi. Escaping mein aap galat ho sakte ho; gair-maujood shell
# mein bug nahi hota.

#: Ye module import karna hi banned hai. `oncall/` ko inki zarurat nahi —
#: har external kaam Docker SDK ya psycopg se hota hai.
_BANNED_MODULES: frozenset[str] = frozenset(
    {
        "subprocess",  # run / Popen / call / check_output — sab
        "pty",         # pty.spawn() bhi shell chala deta hai
        "commands",    # Python 2 ka legacy, ab bhi copy-paste ho jaata hai
    }
)

#: `os.<naam>(...)` — ye function process spawn karte hain.
#: 🔴 `os` khud banned **nahi** hai: `os.environ`, `os.path` bilkul legal hain.
#:    Isliye check module-level nahi, function-level hai.
_BANNED_OS_CALLS: frozenset[str] = frozenset(
    {
        "system",
        "popen",
        "execv", "execve", "execl", "execlp", "execvp", "execvpe",
        "spawnl", "spawnv", "spawnlp", "spawnvp", "posix_spawn",
        "fork", "forkpty",
    }
)


def _dotted_name(node: ast.expr) -> str:
    """`ast.Call` ke `func` ko `"os.system"` jaisa string banao.

    Teen shapes handle hote hain:
        `system(...)`            → Name        → "system"
        `os.system(...)`         → Attribute   → "os.system"
        `x.y.z(...)`             → Attribute   → "x.y.z"

    Baaki kuch bhi (e.g. `funcs["a"](...)`) → khaali string. Wo deliberate
    hai: dynamic dispatch AST se pakda nahi ja sakta, aur ye test jhooth nahi
    bolega ki wo pakad sakta hai. Us gap ko `HANDLERS` dict-dispatch band
    karta hai (handlers.py: `getattr` nahi use kiya, isi wajah se).
    """
    parts: list[str] = []
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        return ""
    return ".".join(reversed(parts))


def _shell_violations(path: Path) -> list[str]:
    """Ek file ke saare shell-related violations, `file:line — kya` format mein."""
    bad: list[str] = []
    for node in ast.walk(_parse(path)):
        # (a) `import subprocess` / `import subprocess as sp`
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _BANNED_MODULES:
                    bad.append(f"{path}:{node.lineno} — import {alias.name}")
        # (b) `from subprocess import run`
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in _BANNED_MODULES:
                bad.append(f"{path}:{node.lineno} — from {node.module} import ...")
        # (c) call sites: `os.system(...)`, `popen(...)`, aur `shell=True`
        elif isinstance(node, ast.Call):
            name = _dotted_name(node.func)
            tail = name.rsplit(".", 1)[-1]
            if name.startswith("os.") and tail in _BANNED_OS_CALLS:
                bad.append(f"{path}:{node.lineno} — {name}(...)")
            # 🔴 Bare `popen(...)` / `system(...)` bhi pakdo — `from os import
            #    system` ke baad wo `os.` prefix ke bina dikhta hai. Ye do naam
            #    kaafi hain; `fork` jaisa aam shabd bare form mein false
            #    positive de sakta tha, aur uska import (a)/(b) mein pakda hi
            #    jaata hai kyunki wo `os` se aata hai.
            elif tail in {"popen", "system"} and "." not in name and name:
                bad.append(f"{path}:{node.lineno} — {name}(...) (bare)")
            for kw in node.keywords:
                # `shell=True` — sirf literal `True` pe. `shell=False` legal hai
                # (bekaar hai, par khatarnaak nahi), aur `shell=x` ko AST resolve
                # nahi kar sakta, toh us par jhoota dawa nahi karenge.
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    bad.append(f"{path}:{node.lineno} — shell=True in {name or '<call>'}(...)")
    return bad


def test_no_shell_under_oncall() -> None:
    """🔒 `oncall/` ke andar shell ka koi raasta nahi hona chahiye.

    ⚠️ Yahan `assert` use hua hai, aur ye `allowlist.py` ke "assert kabhi nahi"
       rule ke khilaaf **nahi** hai. Wo rule production invariants ke liye tha,
       kyunki `python -O` assert hata deta hai. Test files kabhi `-O` ke saath
       nahi chalti (pytest ka poora reporting `assert` pe hi khada hai), aur
       neeche ka `__main__` block bhi normal mode mein chalta hai. Farq ye hai:
       ek production invariant chup-chaap gayab ho jaata; ek test gayab ho toh
       CI mein "0 tests collected" dikhta hai.
    """
    violations = [v for p in _python_files() for v in _shell_violations(p)]
    assert not violations, "CLAUDE.md §3 — no shell in the action path:\n" + "\n".join(violations)


# ─────────────────────────────────────────────────────────────────────────────
# 🔴 Check 2 — `oncall/` `chaos/` se import nahi karta (ground truth invisible)
# ─────────────────────────────────────────────────────────────────────────────
#
# Concept: `chaos/faults.py` mein **answer key** hai — har fault ka
# `correct_action`, `correct_target`, `also_acceptable`. Agar agent ka koi
# module wo import kar le, toh "kya agent cheat kar raha hai?" ka jawab "nahi"
# se "abhi tak nahi" ban jaata — aur wo bhi sirf ek code review pe tikka hota.
# Ye test us jawab ko structural bana deta hai.
#
# 🔴 `import` kaafi hai, `correct_action` shabd dhoondhna zaroori nahi: field
#    tak pahunchne ka **koi** raasta module import ke bina nahi hai. Toh ek
#    hi cheez pe pehra kaafi hai, aur wo pehra chhoota bhi nahi.


def _chaos_imports(path: Path) -> list[str]:
    """Ek file mein `chaos` se aane wale saare imports."""
    bad: list[str] = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "chaos":
                    bad.append(f"{path}:{node.lineno} — import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            # 🔴 `node.level > 0` = relative import (`from .. import chaos`).
            #    `node.module` tab bhi `"chaos"` nahi hota, isliye dono check.
            if (node.module or "").split(".")[0] == "chaos":
                bad.append(f"{path}:{node.lineno} — from {node.module} import ...")
            if node.level and any(a.name == "chaos" for a in node.names):
                bad.append(f"{path}:{node.lineno} — from {'.' * node.level} import chaos")
    return bad


def test_oncall_never_imports_chaos() -> None:
    """🔴 Agent ke process mein answer key load nahi ho sakti."""
    violations = [v for p in _python_files() for v in _chaos_imports(p)]
    assert not violations, (
        "CLAUDE.md §3 — ground truth is invisible to the agent:\n" + "\n".join(violations)
    )


# ─────────────────────────────────────────────────────────────────────────────
# 🔒 Check 3 — verdict path mein koi "judge" nahi
# ─────────────────────────────────────────────────────────────────────────────
#
# CLAUDE.md §0: "A model's opinion is never evidence." Verdict sirf
# `oncall/oracle/health.py` likhta hai — 3 probes, 30 second, load ke neeche.
# LLM-as-judge (yaani ek model se poochhna "kya ye fix ho gaya?") is project
# ki poori thesis ko khokhla kar deta.
#
# 🔴 Ye check baaki dono se **alag kism ka** hai, aur ye farq imaandaari se
#    likhna zaroori hai: yahan hum *naam* dekhte hain, behaviour nahi. Ek
#    function `verify_fix_with_llm()` naam rakh kar ye test bypass kar sakta
#    hai. Toh ye check ek **naming discipline** enforce karta hai, na ki ek
#    proof. Asli guarantee architecture se aati hai: `health.py` hi `verdict`
#    column likhta hai, aur wo file kisi LLM client ko import nahi karti.
#    (Wo import-level check `tests/test_oracle.py` mein aayega.)
#
#    Phir bhi ye rakha hai, kyunki CLAUDE.md §3 mein likha grep-checkable dawa
#    — `grep -rni "judge" oncall/` stays empty — ko machine-checkable rakhna
#    hai. Aur ye ek jagah hai jahan grep sach mein kaam karta tha (kyunki
#    "judge" shabd hamare comments mein bhi nahi hai) — lekin usko test mein
#    daal dena isse CI ka hissa bana deta hai, meri yaad ka nahi.


def _judge_names(path: Path) -> list[str]:
    """Function/class/variable naam jinme "judge" hai."""
    bad: list[str] = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if "judge" in node.name.lower():
                bad.append(f"{path}:{node.lineno} — def/class {node.name}")
        elif isinstance(node, ast.Name) and "judge" in node.id.lower():
            bad.append(f"{path}:{node.lineno} — name {node.id}")
    return bad


def test_no_llm_as_judge_names() -> None:
    """🔒 Koi bhi symbol "judge" naam ka nahi. Verdict oracle ka kaam hai."""
    violations = [v for p in _python_files() for v in _judge_names(p)]
    assert not violations, (
        "CLAUDE.md §3 — the LLM never decides correctness:\n" + "\n".join(violations)
    )


# ─────────────────────────────────────────────────────────────────────────────
# 🔴 Check 0 — ye test khud jhooth na bole
# ─────────────────────────────────────────────────────────────────────────────


def test_files_were_actually_scanned() -> None:
    """Guard ka guard: 0 files scan karke "pass" bolna sabse bura outcome hai.

    Sochne layak scenario: kal `oncall/` ka naam badla jaata hai (CLAUDE.md §7
    ka pending `mv SETU oncall` rename), `ONCALL` path galat ho jaata hai,
    `rglob` khaali list deta hai — aur upar ke teen test **pass** ho jaate
    hain, kyunki khaali list mein koi violation nahi hota. Teen green tick,
    zero verification. Isliye ye chautha test.

    Threshold 5 kyun: aaj `oncall/` mein `__init__.py`, `config.py`,
    `actions/allowlist.py`, `actions/handlers.py`, `llm/schema.py` hain — 5+
    (package `__init__.py` files alag se). Ye number badhega, kabhi ghatega
    nahi, toh `>= 5` ek safe floor hai jo maintenance nahi maangta.
    """
    files = _python_files()
    assert ONCALL.is_dir(), f"{ONCALL} directory nahi mili — path constant galat hai?"
    assert len(files) >= 5, f"sirf {len(files)} file scan hui, kam se kam 5 honi chahiye: {files}"


# ─────────────────────────────────────────────────────────────────────────────
# 🔴 Negative control — guard sach mein pakadta hai ya bas green dikhta hai
# ─────────────────────────────────────────────────────────────────────────────
#
# Concept (CLAUDE.md §1): ek test jo saaf code pe pass hota hai, wo do wajahon
# se pass ho sakta hai — (a) code sach mein saaf hai, ya (b) test kuch dekhta
# hi nahi. Bahar se dono ek jaise dikhte hain: green tick.
#
# **Negative control** wo experiment hai jahan aap jaan-boojh kar bimaari daal
# kar dekhte ho ki test lal hota hai ya nahi. Medicine se aaya hua idea:
# ek COVID test jo har sample pe "negative" bolta hai, 99% accurate lagta hai
# aur bilkul bekaar hai. Usi ko yahan lagaya: 17 tootay hue samples andar
# daalte hain, aur zaroori ye hai ki **saare 17 pakde jaayein**.
#
# Aur ulta bhi zaroori hai (false positives): 6 saaf samples jinme banned
# shabd **comment, docstring aur plain string** ke roop mein maujood hain.
# Agar test un par bhi fail ho jaaye, toh hum wapas grep ki jagah pahunch gaye
# — aur wahi wo bug hai jiski wajah se ye poori file likhi gayi.
#
# 🔴 Ye check ek alag shell command ke roop mein bhi likha ja sakta tha, aur
#    maine wo pehle chalaya bhi. Lekin ek verification jo sirf mere terminal
#    history mein hai, wo kal maujood nahi hoti. File ke andar hai, toh har
#    baar chalti hai.

#: (filename, source) — har ek mein exactly ek violation.
_BAD_SAMPLES: tuple[tuple[str, str], ...] = (
    ("import.py", "import subprocess\n"),
    ("from.py", "from subprocess import run\n"),
    ("alias.py", "import subprocess as sp\n"),
    ("submod.py", "import subprocess.foo\n"),
    ("pty.py", "import pty\npty.spawn('/bin/sh')\n"),
    ("ossystem.py", "import os\nos.system('rm -rf /')\n"),
    ("ospopen.py", "import os\nos.popen('ls')\n"),
    ("osexec.py", "import os\nos.execv('/bin/sh', [])\n"),
    ("bare.py", "from os import system\nsystem('ls')\n"),
    ("shelltrue.py", "import docker\ndocker.x(cmd, shell=True)\n"),
    ("shelltrue_kw.py", "run(['ls'], shell=True)\n"),
    ("chaos_from.py", "from chaos.faults import FAULTS\n"),
    ("chaos_mod.py", "import chaos\n"),
    ("chaos_rel.py", "from .. import chaos\n"),
    ("judge_fn.py", "def llm_judge(x):\n    return x\n"),
    ("judge_cls.py", "class VerdictJudge:\n    pass\n"),
    ("judge_var.py", "judge_result = 1\n"),
)

#: Saaf samples. 🔥 Teen mein banned shabd **maujood** hain — comment,
#: docstring, aur plain string ke roop mein. Yahi wo teen case hain jinpe
#: grep fail hota tha aur AST pass hota hai.
_CLEAN_SAMPLES: tuple[tuple[str, str], ...] = (
    ("os_ok.py", "import os\nprint(os.environ['X'], os.path.join('a', 'b'))\n"),
    ("shell_false.py", "run(['ls'], shell=False)\n"),
    ("docstring.py", '"""subprocess os.system popen shell=True chaos judge — sirf text."""\n'),
    ("comment.py", "# subprocess os.system popen shell=True import chaos judge\nx = 1\n"),
    ("string.py", "s = 'shell=True'\nt = 'import chaos'\n"),
    ("real_action.py", "cmd = ['find', '/tmp', '-mindepth', '1', '-delete']\n"),
)


def _all_violations(path: Path) -> list[str]:
    """Teeno checks ek file pe."""
    return _shell_violations(path) + _chaos_imports(path) + _judge_names(path)


def test_guard_catches_planted_violations(tmp_path: Path | None = None) -> None:
    """17 tootay hue samples pakde jaane chahiye, 6 saaf samples chhoote rehne chahiye.

    `tmp_path` pytest ki fixture hai (wo apne aap ek fresh temp directory deta
    hai). Default `None` isliye rakha ki `__main__` block bhi isi function ko
    bina argument bula sake — tab hum khud `tempfile` se directory banate hain.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        base = Path(td) if tmp_path is None else tmp_path
        missed: list[str] = []
        false_positives: list[str] = []

        for name, src in _BAD_SAMPLES:
            f = base / f"bad_{name}"
            f.write_text(src, encoding="utf-8")
            if not _all_violations(f):
                missed.append(name)
            f.unlink()

        for name, src in _CLEAN_SAMPLES:
            f = base / f"clean_{name}"
            f.write_text(src, encoding="utf-8")
            hits = _all_violations(f)
            if hits:
                false_positives.append(f"{name}: {hits}")
            f.unlink()

    assert not missed, f"guard in violations ko MISS kar gaya: {missed}"
    assert not false_positives, f"guard ne saaf code pe jhooti alarm di: {false_positives}"


# ─────────────────────────────────────────────────────────────────────────────
# pytest ke bina bhi chalta hai
# ─────────────────────────────────────────────────────────────────────────────
#
# 🔴 Ye block kyun likha: aaj is machine pe pytest **install nahi hai**
# (`requirements.txt` mein pinned hai, lekin `make install` abhi chalna hai).
# Aur maine `handlers.py` + `allowlist.py` ke docstrings mein likh diya hai ki
# "verify aise hota hai". CLAUDE.md §2: "a verification command that fails is
# worse than no command." Toh is file ko **zero dependencies** pe chalna hi
# chahiye — sirf `ast` aur `pathlib`, dono stdlib.
#
# Ye ek asli trade-off hai, aur uska doosra pehlu bhi likh dena chahiye:
# pytest ke bina hume fixtures, parametrize, assertion-rewriting (jo bataata
# hai ki assert ke andar kaunsi value kya thi) kuch nahi milta. Isliye har
# assert ka apna explicit message hai — pytest ke smart output ka sasta
# replacement, lekin kaam chal jaata hai.

if __name__ == "__main__":
    _checks = (
        test_files_were_actually_scanned,
        test_guard_catches_planted_violations,
        test_no_shell_under_oncall,
        test_oncall_never_imports_chaos,
        test_no_llm_as_judge_names,
    )
    _failed = 0
    print(f"scanning {len(_python_files())} file(s) under {ONCALL}\n")
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
    print(f"saare {len(_checks)} structural check pass hue")
