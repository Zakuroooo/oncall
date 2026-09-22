"""chaos/inject.py — fault inject/heal CLI. Ek incident ka JANM yahan hota hai.

─────────────────────────────────────────────────────────────────────────────
Ye file kya karti hai, ek line mein
─────────────────────────────────────────────────────────────────────────────
Ek fault id leti hai (`F1`…`F5`), `chaos/faults.py` ka `break_fn()` chalati hai,
aur control-plane DB mein ek `incidents` row + ek `events` row + ek `jobs` row
banati hai — teeno EK transaction mein. Uske baad worker us job ko uthata hai aur
state machine chalne lagti hai.

─────────────────────────────────────────────────────────────────────────────
🔴 Teen design faisle jo yahan chhupe hain — inhe samajhna zaruri hai
─────────────────────────────────────────────────────────────────────────────

**1. `incidents.service` = `"shopapp"` HAMESHA, chahe kharaabi kahin aur ho.**

Ye ek line poore project ki localization measurement bachati hai. F5 mein asli
culprit `shopcache` hai; F3 mein `shopdb` hai. Agar main `incidents.service` mein
asli culprit likh deta, toh agent (jo isi row ko padhta hai) ko target **muft
mein** mil jaata — aur "kya agent ne sahi service pehchani" ek measurement ki
jagah ek tautology ban jaata. `service` ka matlab hai *"kahan se symptom dikha"*,
*"kaun kharaab hai"* nahi. Wahi farq pata karna agent ka kaam hai.

Yahi soch `stack/oncalldb/init.sql:30–41` mein `fault_class` ke liye likhi hui
hai. Ye us soch ka doosra aadha hissa hai.

**2. `--write` ke bina ye script kuch nahi karti** (`CLAUDE.md` §3).

Aur yahan wo rule DB ke liye kam, **containers** ke liye zyada zaruri hai: ek
galat `python -m chaos.inject F4` shopdb ka `max_connections` badal kar use
restart kar deta. Dry-run poora plan print karta hai — kaunse container, kaunsi
SQL, kaunsi row — bina kuch chhue.

**3. `correct_action` default mein print NAHI hota.** `--spoil` chahiye.

Wajah bilkul practical hai: demo recording, terminal scrollback, aur copy-paste
kiya hua output — ye sab kabhi LLM ke prompt mein ja sakte hain. Ground truth ko
ek flag ke peeche rakhne se boundary **dikhti** hai, sirf likhi hui nahi rehti.

⚠️ Ye file `chaos/` mein hai, `oncall/` mein nahi — isliye ye `chaos.faults`
import kar sakti hai. Ulta kabhi nahi (`CLAUDE.md` §3, `make lint` check #3).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass

from chaos.faults import FAULTS, Fault, fault_ids

# 🔴 DIRECTION MATTERS. Ye import `chaos` → `oncall` hai, aur wo **allowed** hai.
#    Jo BANNED hai wo ulta hai: `oncall` → `chaos` (`CLAUDE.md` §3 — "nothing under
#    oncall/ may import from chaos/"). Wajah asymmetric hai: agent ko ground truth
#    nahi dikhni chahiye; chaos tool ko app ka DSN dikhna bilkul theek hai.
#    Alternative jo maine reject kiya: yahan `os.environ["ONCALL_DB_URL"]` dobara
#    padhna. Usse do default values ban jaati (yahan ek, config.py mein doosri) aur
#    wo chupchaap drift karti — "kaam kar raha tha, aaj kyun nahi" wala bug.
from oncall.config import settings

# ── psycopg optional hai, jaan-boojh kar ────────────────────────────────────
# `scripts/seed_shopdb.py` mein import fail hone pe poori script `sys.exit` karti
# hai. Yahan **nahi**, aur ye farq deliberate hai: `--list` aur dry-run ko DB ki
# zaroorat hi nahi hai. Agar main yahan bhi exit kar deta, toh bina venv wale
# machine pe `--list` bhi na chalta — jabki uska DB se koi lena-dena nahi.
# Error tab aata hai jab asli mein connect karne ki koshish hoti hai (§write path).
# Wahi soch `chaos/faults.py` ke optional `docker` import mein bhi hai.
try:
    import psycopg
except ModuleNotFoundError:  # pragma: no cover - install state pe depend karta hai
    psycopg = None  # type: ignore[assignment]

# ── 🔴 Ye constant project ka ek measurement bachati hai ────────────────────
# `incidents.service` = "kahan se symptom dikha", NOT "kaun kharaab hai".
# Paanchon faults shopapp ke `/health` se dikhte hain, chahe culprit shopdb
# (F3, F4) ya shopcache (F5) ho. Isliye ye ek CONSTANT hai — per-fault value
# nahi. Per-fault hota toh row padhte hi target mil jaata.
SERVICE_UNDER_OBSERVATION = "shopapp"

# Naya incident kis state mein janm leta hai. `init.sql` ke 10 allowed states
# mein se pehla. Worker isi ko dekh kar aage badhta hai.
INITIAL_STATE = "DETECTED"


def _check_no_localization_leak() -> None:
    """Import pe chalta hai. Ye check ek MEASUREMENT ka guard hai, code ka nahi.

    Sawaal jo ye poochta hai: *"kya `incidents.service` padh kar target guess
    kiya ja sakta hai?"* Agar paanchon faults ka `correct_target` bhi wahi
    `"shopapp"` hota jo `service` hai, toh "agent ne sahi service localize ki"
    ek khaali claim hoti — har jawaab automatically sahi.

    🔴 `assert` jaan-boojh kar NAHI, `raise` hai. `python -O` asserts ko strip
       kar deta hai, aur strip hone wala guard woh guard hai jo production mein
       kabhi nahi chalta. Wahi wajah `chaos/faults.py:_check_registry()` mein
       likhi hui hai.
    """
    targets = {f.correct_target for f in FAULTS.values() if f.correct_target}
    if len(targets) < 2:
        raise RuntimeError(
            "chaos/inject.py leak guard fail: sab faults ka correct_target ek hi "
            f"hai ({targets}). Iska matlab localization measure karne layak nahi "
            "bacha — kisi ne registry ya ye constant badla hai."
        )
    if targets == {SERVICE_UNDER_OBSERVATION}:
        raise RuntimeError(
            "chaos/inject.py leak guard fail: correct_target hamesha "
            f"'{SERVICE_UNDER_OBSERVATION}' hai, jo incidents.service ke barabar "
            "hai. Agent ko target muft mein mil jaayega."
        )


_check_no_localization_leak()


@dataclass(frozen=True)
class InjectResult:
    """Ek injection ka nateeja, printable form mein.

    `frozen=True` isliye ki ye ek *record* hai, ek mutable scratchpad nahi —
    banne ke baad koi ise badal na sake. Wahi soch `Fault` dataclass mein bhi.
    """

    fault_id: str
    #  🔴 dry-run mein `incident_id` None rehta hai. `0` nahi — kyunki `0` ek
    #     asli id jaisa dikhta hai aur `if incident_id:` jaisi line usse chupchaap
    #     "koi id nahi" maan legi. None ka matlab saaf hai: row bani hi nahi.
    incident_id: int | None
    written: bool
    break_output: str
    plan: tuple[str, ...] = ()


# ═════════════════════════════════════════════════════════════════════════════
# 1. Lookup — ek galat fault id ka jawaab ek achha error message hai
# ═════════════════════════════════════════════════════════════════════════════


def _fault(fault_id: str) -> Fault:
    """Fault id → Fault. `f4` bhi chalta hai, `F4` bhi.

    `.upper()` ek chhoti si suvidha hai jiska badla bada hai: ye script raat ke
    2 baje haath se chalayi jaati hai. Lekin `strip()` ke saath, warna copy-paste
    ki hui trailing space ek "F4 nahi mila" deti hai jo poori jhooti lagti hai.
    """
    key = fault_id.strip().upper()
    if key not in FAULTS:
        # 🔴 `raise SystemExit("message")` yahan JAAN-BOOJH KAR nahi hai, do wajah se —
        #    aur dono ek test ne pakdi thi, meri soch ne nahi:
        #
        #    1. **Exit code jhoot bolta hai.** `SystemExit` ko string do toh Python
        #       exit code **1** deta hai aur `exc.code` us *string* ko rakhta hai.
        #       Matlab `main()` ka `int` return-type contract toot jaata hai, aur
        #       ek caller jo `if code == 1:` likhta hai wo galat compare karta hai.
        #    2. **Message capture nahi hota.** String wala `SystemExit` interpreter
        #       khud print karta hai, hamara code nahi — process ke marne ke baad.
        #       Toh `contextlib.redirect_stderr` (jo test use karta hai) usse pakad
        #       hi nahi sakta, aur "kya error message madadgar tha?" untestable
        #       ban jaata hai. Ek message jo test na ho sake, wo aadhi feature hai.
        #
        #    Isliye: pehle KHUD stderr pe print karo, phir saaf integer se maro.
        print(
            f"❌ fault '{fault_id}' nahi hai. Available: {', '.join(fault_ids())}\n"
            f"   Detail dekhne ke liye: python -m chaos.inject --list",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return FAULTS[key]


# ═════════════════════════════════════════════════════════════════════════════
# 2. Dry-run plan — ye function project ka "trust me" wala hissa hai
# ═════════════════════════════════════════════════════════════════════════════
#
# 🔴 `--write` ke bina kuch nahi hota — lekin us waade ki keemat tabhi hai jab
#    dry-run SACH batata ho ki kya hota. Ek adhoora plan ("kuch containers
#    chhue jaayenge") ek jhoota plan hai. Isliye ye function teen cheezein
#    naam-se batata hai: container, DB rows, aur reversal ka tareeka.


def _redact_dsn(dsn: str) -> str:
    """`postgresql://user:pass@host/db` → `postgresql://user:***@host/db`.

    🔴 Dry-run ka output copy-paste hota hai — issue mein, chat mein, demo
       recording mein. Local dev password `oncall` koi raaz nahi hai, lekin ye
       function aadat ke liye hai: jo string print hoti hai usme credential
       kabhi nahi. Wahi soch `docs/THREAT_MODEL.md` mein hai.

    Jaan-boojh kar `urllib.parse` nahi: ek malformed DSN pe `urlsplit` exception
    de sakta hai, aur ek dry-run printer ka crash hona bekaar hai. Ye version
    kuch bhi ho, kuch return karta hai.
    """
    if "://" not in dsn or "@" not in dsn:
        return dsn
    scheme, rest = dsn.split("://", 1)
    creds, _, tail = rest.partition("@")
    if ":" not in creds:
        return dsn
    user, _, _pw = creds.partition(":")
    return f"{scheme}://{user}:***@{tail}"


def build_plan(fault: Fault, *, with_incident: bool, dsn: str) -> tuple[str, ...]:
    """Wo saari cheezein jo `--write` ke saath HOTI, ek list mein.

    Ye jaan-boojh kar ek `tuple[str, ...]` return karta hai, seedha `print` nahi.
    Wajah: is tarah `--dry-run` ka output aur `InjectResult.plan` ek hi source se
    aate hain. Do jagah likha hua plan do jagah drift karta hai.
    """
    lines: list[str] = [
        f"fault      : {fault.id} — {fault.title}",
        f"symptom    : {fault.symptom}",
        f"container  : {', '.join(fault.touches)} ← ye chhue jaayenge",
        f"reversal   : python -m chaos.inject {fault.id} --heal --write",
    ]
    if with_incident:
        lines += [
            "",
            f"DB         : {_redact_dsn(dsn)}",
            f"  incidents  +1 row  service='{SERVICE_UNDER_OBSERVATION}' "
            f"fault_class='{fault.id}' state='{INITIAL_STATE}' attempt=0",
            "  events     +1 row  kind='state_change'  (timeline ka pehla entry)",
            "  jobs       +1 row  status='queued'      (worker isko uthayega)",
            "  ⚠️ teeno EK transaction mein — ya teeno banti hain ya ek bhi nahi",
        ]
    else:
        lines += [
            "",
            "DB         : ❌ koi row nahi (--no-incident). Sirf container toota.",
        ]
    return tuple(lines)


# ═════════════════════════════════════════════════════════════════════════════
# 3. DB write — 🔴 teen rows, EK transaction. Ye "kyun" ka jawab lamba hai.
# ═════════════════════════════════════════════════════════════════════════════
#
# Teen rows banti hain aur teeno ka ek saath bharosa hai:
#
#   incidents  = "ye hua"          (audit + UI ka row)
#   events     = "ye kab hua"      (timeline ka pehla entry)
#   jobs       = "isko uthao"      (queue ka signal)
#
# 🔴 Agar ye teen alag-alag commit hoteen, do failure modes khulte, aur dono
#    chup-chaap hote — jo iss project ka sabse napasand kism ka bug hai:
#
#   (a) `incidents` bani, `jobs` nahi → ek incident jo UI mein DETECTED dikhta
#       hai aur hamesha DETECTED rehta hai. Koi error nahi, koi log nahi. Wahi
#       "silent hang" jiska darr `init.sql:56–58` ke state CHECK mein likha hai.
#   (b) `jobs` bani, `incidents` nahi → asambhav, kyunki FK hai. Lekin FK ka
#       ye faayda sirf tab hai jab dono ek transaction mein hon.
#
# Isliye `with conn.transaction():` — psycopg ka explicit transaction block.
# Alternative jo maine reject kiya: teen `conn.commit()`. Wo chhota dikhta hai
# aur (a) ko possible bana deta hai.


def _create_incident(
    fault: Fault,
    *,
    dsn: str,
    seed: int,
    rag_enabled: bool,
) -> int:
    """Teen rows likhta hai aur naya `incidents.id` return karta hai.

    🔴 Ye function tabhi call hota hai jab `--write` diya gaya ho. Iske andar
       koi `if write:` check nahi hai — jaan-boojh kar. Ek function jo "likhun ya
       na likhun" khud decide karta hai, wo do kaam karta hai; aur uska naam
       jhoot bolta hai. Faisla caller ka, likhna iska.
    """
    if psycopg is None:  # pragma: no cover - install state pe depend karta hai
        raise SystemExit(
            "❌ Dependency missing: psycopg\n"
            "   DB row likhne ke liye venv chahiye. Setup:\n"
            "     make venv && make install\n"
            "     source .venv/bin/activate\n"
            "   (`--list` aur dry-run bina iske chalte hain.)"
        )

    with psycopg.connect(dsn) as conn:
        # `with conn.transaction():` — ye block bina exception khatam hua toh
        # COMMIT, exception aaya toh ROLLBACK. Teeno rows ya saari, ya koi nahi.
        with conn.transaction():
            # 🔴 `%s` placeholders, f-string NAHI. Ye SQL injection ka bachaav
            #    hai. "Lekin ye local script hai, input mera hai" — sahi hai,
            #    aur phir bhi: ye aadat hai, aur `fault.id` kal kisi HTTP request
            #    se aa sakti hai (`POST /api/faults/{fault_id}/inject`,
            #    `docs/CODEMAP.md:429`). Wahan ye same function chalega.
            row = conn.execute(
                """
                INSERT INTO incidents (service, fault_class, state, attempt,
                                       rag_enabled, seed)
                VALUES (%s, %s, %s, 0, %s, %s)
                RETURNING id
                """,
                (SERVICE_UNDER_OBSERVATION, fault.id, INITIAL_STATE, rag_enabled, seed),
            ).fetchone()
            if row is None:  # pragma: no cover - RETURNING ke saath ho nahi sakta
                raise RuntimeError("INSERT ... RETURNING id ne kuch return nahi kiya")
            incident_id = int(row[0])

            # Timeline ka pehla entry. `from` None hai kyunki isse pehle koi
            # state nahi thi — incident ka janm yahi hai.
            # ⚠️ `payload` mein `fault_class` dobara likha hai (incidents mein
            #    already hai). Duplication jaan-boojh kar: timeline ek standalone
            #    audit record hai, aur ek audit line jo doosri table ke bina
            #    padhi na ja sake, aadhi audit line hai.
            conn.execute(
                """
                INSERT INTO events (incident_id, kind, payload)
                VALUES (%s, 'state_change', %s::jsonb)
                """,
                (
                    incident_id,
                    # 🔴 `json.dumps` + `::jsonb` cast, `psycopg.types.json.Jsonb`
                    #    nahi. Do wajah: (1) psycopg3 plain `dict` ko khud jsonb
                    #    nahi banata, toh kuch toh chahiye hi tha; (2) `Jsonb`
                    #    ke liye ek aur import chahiye jo optional-import block
                    #    mein add hota, jabki `json` stdlib mein hai aur hamesha
                    #    maujood hai. Cast SQL mein dikhta hai — padhne mein saaf.
                    json.dumps(
                        {
                            "from": None,
                            "to": INITIAL_STATE,
                            "by": "chaos.inject",
                            "fault_class": fault.id,
                            "note": fault.symptom,
                        }
                    ),
                ),
            )

            # Queue signal. `status` ka default bhi 'queued' hai, phir bhi            # explicitly likha — taaki is line ko padh kar pata chale ki row
            # queued state mein janm le rahi hai, `init.sql` kholne ki zaroorat
            # na pade.
            conn.execute(
                "INSERT INTO jobs (incident_id, status) VALUES (%s, 'queued')",
                (incident_id,),
            )

    return incident_id


# ═════════════════════════════════════════════════════════════════════════════
# 4. Public API
# ═════════════════════════════════════════════════════════════════════════════


def inject(
    fault_id: str,
    *,
    write: bool = False,
    with_incident: bool = True,
    seed: int | None = None,
    rag_enabled: bool | None = None,
    dsn: str | None = None,
) -> InjectResult:
    """Ek fault daalo. `write=False` (default) pe sirf plan banta hai.

    🔴 Saare optional arguments keyword-only hain (`*` ke baad). Wajah wahi jo
       `oncall/actions/handlers.py` mein hai: `inject("F4", True)` padh kar koi
       nahi bata sakta ki wo `True` kya hai — `write`? `with_incident`? Ek
       positional boolean ek future bug hai. `inject("F4", write=True)` saaf hai.

    `seed`/`rag_enabled`/`dsn` `None` par settings se aate hain. `None` default
    hai, seedha `settings.seed` nahi — kyunki default argument function DEFINE
    hone ke waqt evaluate hota hai, aur tab env var badalna bekaar ho jaata.
    """
    fault = _fault(fault_id)
    seed = settings.seed if seed is None else seed
    rag_enabled = settings.rag_enabled if rag_enabled is None else rag_enabled
    dsn = settings.oncall_db_url if dsn is None else dsn

    plan = build_plan(fault, with_incident=with_incident, dsn=dsn)
    if not write:
        return InjectResult(
            fault_id=fault.id, incident_id=None, written=False, break_output="", plan=plan
        )

    # 🔴 ORDER MATTERS: pehle container todo, PHIR DB row banao.
    #    Ulta karne se ek window khulti hai jahan incident row maujood hai aur
    #    worker use uthata hai, lekin service abhi theek hai — worker health
    #    check karta hai, sab green paata hai, aur incident galat tareeke se
    #    band ho jaata. Ek race jo theek demo ke beech dikhti hai.
    #    Is order mein ulti problem hai (fault laga, row nahi bani) — lekin wo
    #    LOUD hai: service kharaab dikhti hai aur UI mein kuch nahi aata.
    #    Ek dikhne wala failure ek chhupe hue race se behtar hai.
    break_output = fault.break_fn()

    incident_id = None
    if with_incident:
        incident_id = _create_incident(fault, dsn=dsn, seed=seed, rag_enabled=rag_enabled)

    return InjectResult(
        fault_id=fault.id,
        incident_id=incident_id,
        written=True,
        break_output=break_output,
        plan=plan,
    )


def heal(fault_id: str, *, write: bool = False) -> str:
    """Ek fault ko theek karo. 🔴 Koi DB row NAHI banti/badalti.

    Wajah samajhna zaruri hai: `heal()` ek **operator ka reset button** hai, agent
    ka fix nahi. Agar heal `incidents.state = 'RECOVERED'` likh deta, toh mere
    manual reset aur agent ke asli fix ek jaise dikhte metrics mein — aur N1
    (auto-fix rate) mein mere apne haath ke fix ginne lagte. Wo number tab jhoot
    hota. Agent ka verdict SIRF `oncall/oracle/health.py` likhta hai.
    """
    fault = _fault(fault_id)
    if not write:
        return (
            f"[dry-run] {fault.id} heal hota: {', '.join(fault.touches)} chhue jaate.\n"
            f"           Asli mein karne ke liye: --write"
        )
    return fault.heal_fn()


def heal_all(*, write: bool = False) -> list[str]:
    """Sab kuch normal pe. Demo se pehle aur baad mein chalane wali command.

    Order `fault_ids()` ka hai (F1→F5) aur wo theek hai: heal functions ek
    doosre se independent hain. ⚠️ Ek exception jaanne layak hai — F4 ka heal
    shopapp ko restart karta hai (`touches` mein dono naam isliye hain), toh
    F4 ke baad shopapp thoda naya hota hai. Koi fault iss se toota nahi.
    """
    out: list[str] = []
    for fid in fault_ids():
        try:
            out.append(f"{fid}: {heal(fid, write=write)}")
        except Exception as exc:  # noqa: BLE001
            # 🔴 Yahan broad `except` jaan-boojh kar. `heal_all` ka poora point
            #    "sab kuch wapas normal" hai. Agar F2 ka heal fail ho (container
            #    hi nahi hai) aur main exception upar phenk doon, toh F3–F5 kabhi
            #    heal nahi hote — yaani ek chhoti si galti poore stack ko toota
            #    chhod deti. Isliye: har fault try karo, saari galtiyan report
            #    karo, koi chhupao mat.
            out.append(f"{fid}: ❌ {type(exc).__name__}: {exc}")
    return out


def list_faults(*, spoil: bool = False) -> str:
    """Paanch faults ka table. 🔴 `spoil=False` pe ground truth nahi dikhti.

    Ye default is project ka ek chhota lekin asli boundary hai. `correct_action`
    aur `correct_target` = answer key. Wo terminal output mein aa gayi toh demo
    recording, scrollback aur copy-paste ke through kabhi kisi prompt mein ja
    sakti hai — aur phir "LLM ne khud socha ya usse bata diya gaya" ka jawab
    nahi bachta. Ek flag ke peeche rakhne se boundary DIKHTI hai.

    ⚠️ Ye ek convenience guard hai, security control nahi. `chaos/faults.py`
       khol kar koi bhi answer key padh sakta hai — aur usse koi problem nahi,
       kyunki asli guard `oncall/` mein hai (`make lint` check #3/#4: `oncall/`
       `chaos/` ko import nahi kar sakta). Ye flag insaan ki galti rokta hai,
       hamlavar ko nahi.
    """
    lines: list[str] = []
    for fid in fault_ids():
        f = FAULTS[fid]
        lines += [
            f"{f.id}  {f.title}",
            f"    symptom          : {f.symptom}",
            f"    restart se fix?  : {'HAAN (baseline)' if f.restart_alone_fixes else 'NAHI'}",
            f"    load-dependent?  : {'HAAN 🔥 (N1 ka case)' if f.load_dependent else 'nahi'}",
            f"    chhuta hai       : {', '.join(f.touches)}",
        ]
        if f.why_restart_fails:
            lines.append(f"    restart kyun kaafi nahi: {f.why_restart_fails}")
        if spoil:
            lines += [
                f"    🔓 correct_action: {f.correct_action}  target={f.correct_target}",
                f"    🔓 also_acceptable: {f.also_acceptable or '(koi nahi)'}",
                f"    🔓 relevant_runbooks: {', '.join(f.relevant_runbooks)}",
            ]
        lines.append("")

    if not spoil:
        # 🔴 Ye line pehle field ke naam leti thi ("correct_action/target chhupe hue
        #    hain"). Test ne pakda, aur test sahi tha — do wajah se:
        #
        #    1. **Hint khud ek leak hai.** Jo cheez chhupa rahe ho, uska *naam* de
        #       dena reader ko exactly wo grep string de deta hai jo answer key
        #       tak le jaati hai. Ek taala jiske saath chaabi ka pata likha ho.
        #    2. **Invariant simple rehna chahiye.** Test ka rule ek line ka hai:
        #       "bina --spoil ke output mein answer-key ka koi field naam nahi".
        #       Agar hum is hint ke liye exception banate, toh rule do line ka ho
        #       jaata — aur exception waale rule ko yaad rakhna padta hai. Output
        #       badalna sasta tha, rule kamzor karna mehnga.
        lines.append("🔒 Ground truth chhupi hui hai. Dekhne ke liye: --spoil")
    return "\n".join(lines)


# ═════════════════════════════════════════════════════════════════════════════
# 5. CLI
# ═════════════════════════════════════════════════════════════════════════════


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m chaos.inject",
        description="OnCall chaos CLI — fault daalo, theek karo, ya list dekho.",
        # RawDescription taaki epilog ke examples apni line pe rahein; warna
        # argparse unhe ek paragraph mein jod deta hai aur command copy-paste
        # karne layak nahi rehti.
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m chaos.inject --list                 # 5 faults, ground truth ke bina\n"
            "  python -m chaos.inject F4                     # dry-run: kya hota, kuch nahi karta\n"
            "  python -m chaos.inject F4 --write             # asli injection + incident row\n"
            "  python -m chaos.inject F4 --heal --write      # F4 theek karo\n"
            "  python -m chaos.inject --heal-all --write     # sab normal pe wapas\n"
            "  python -m chaos.inject F1 --write --no-incident   # sirf container todo\n"
            "\n"
            "🔴 --write ke bina kuch nahi hota (CLAUDE.md §3).\n"
        ),
    )
    p.add_argument("fault", nargs="?", help=f"fault id: {', '.join(fault_ids())}")
    p.add_argument(
        "--write",
        action="store_true",
        help="asli mein karo. Iske bina sirf plan print hota hai.",
    )
    p.add_argument("--heal", action="store_true", help="fault theek karo (DB row nahi banti)")
    p.add_argument("--heal-all", action="store_true", help="paanchon theek karo")
    p.add_argument("--list", action="store_true", help="faults ka table")
    p.add_argument(
        "--spoil",
        action="store_true",
        help="🔓 --list ke saath ground truth bhi dikhao (default: chhupi hui)",
    )
    p.add_argument(
        "--no-incident",
        action="store_true",
        help="container todo lekin incidents/events/jobs rows na banao",
    )
    p.add_argument("--seed", type=int, default=None, help=f"default: {settings.seed}")
    # 🔴 `--no-rag` ek store_true hai jo baad mein invert hota hai, `--rag` ka
    #    ulta nahi. Wajah: default `True` hai (settings se), aur argparse mein
    #    `--rag` banane se "diya nahi gaya" aur "False diya gaya" ek jaise dikhte.
    p.add_argument(
        "--no-rag",
        action="store_true",
        help="is incident ka rag_enabled=False (ablation ka arm B)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """Exit code return karta hai, `sys.exit` khud nahi karta.

    Wajah: is tarah `main()` ek test se call ho sakta hai aur uska return value
    check kiya ja sakta hai. `sys.exit` andar hota toh har test ko
    `pytest.raises(SystemExit)` mein lapetna padta.
    """
    args = _build_parser().parse_args(argv)

    # 🔴 Silently-wrong ko usage error banao. `--heal-all` ke saath ek fault id
    #    dena contradiction hai: "sirf F4" aur "sab paanch" ek saath nahi ho sakte.
    #    Pehle heal-all chup-chaap jeet jaata tha aur `F4` ignore ho jaata tha —
    #    yaani `--write` ke saath user 2 container expect karta aur 3 chhue jaate.
    #    Ek galat kaam chup-chaap karne se acha hai mana kar dena.
    #    (argparse ka `add_mutually_exclusive_group` yahan kaam nahi karta —
    #     wo positional `fault` ko flag ke against exclude nahi kar sakta.)
    if args.heal_all and (args.fault or args.heal):
        print(
            "❌ --heal-all akela chalta hai. Ek fault id ya --heal uske saath nahi.\n"
            "   Sab theek karne ke liye : python -m chaos.inject --heal-all --write\n"
            "   Sirf ek ke liye         : python -m chaos.inject F4 --heal --write",
            file=sys.stderr,
        )
        return 2

    if args.list and (args.fault or args.heal or args.heal_all):
        print(
            "❌ --list sirf padhta hai, kuch karta nahi. Usko akela chalao.",
            file=sys.stderr,
        )
        return 2

    if args.list:
        print(list_faults(spoil=args.spoil))
        return 0

    if args.heal_all:
        for line in heal_all(write=args.write):
            print(line)
        if not args.write:
            print("\n🔴 dry-run tha. Asli mein karne ke liye --write lagao.")
        return 0

    if not args.fault:
        # 🔴 `parser.error()` nahi, khud ka message — kyunki argparse ka
        #    "the following arguments are required" yahan jhoot hota: `fault`
        #    required nahi hai (`--list` bina uske chalta hai). Asli baat ye hai
        #    ki teen mein se ek toh chahiye.
        print(
            "❌ Kuch batao: ek fault id, ya --list, ya --heal-all.\n"
            f"   Fault ids: {', '.join(fault_ids())}\n"
            "   Help: python -m chaos.inject --help",
            file=sys.stderr,
        )
        return 2

    if args.heal:
        print(heal(args.fault, write=args.write))
        return 0

    result = inject(
        args.fault,
        write=args.write,
        with_incident=not args.no_incident,
        seed=args.seed,
        rag_enabled=False if args.no_rag else None,
    )

    if not result.written:
        print("🔍 DRY-RUN — kuch nahi hua.\n")
        for line in result.plan:
            # 🔴 Khaali line ko indent nahi karte. `f"  {line}"` ek blank entry ko
            #    do spaces bana deta hai — invisible trailing whitespace, jo diff
            #    aur copy-paste dono mein gandagi hai.
            print(f"  {line}" if line else "")
        print("\n🔴 Asli mein karne ke liye --write lagao.")
        return 0

    print(f"💥 {result.fault_id} inject ho gaya: {result.break_output}")
    if result.incident_id is not None:
        print(f"📋 incident #{result.incident_id} bani (state={INITIAL_STATE}, job queued)")
        print("   Worker isko uthayega. Timeline: http://127.0.0.1:8000/")
    else:
        print("📋 koi incident row nahi (--no-incident)")
    print(f"↩️  Wapas theek karne ke liye: python -m chaos.inject {result.fault_id} --heal --write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
