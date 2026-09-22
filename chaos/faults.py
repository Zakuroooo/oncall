"""chaos/faults.py — 5 fault classes, aur unka GROUND TRUTH.

─────────────────────────────────────────────────────────────────────────────
🔴 Ye file `oncall/` ke liye INVISIBLE hai. Ye sabse zaruri baat hai.
─────────────────────────────────────────────────────────────────────────────
`CLAUDE.md` §3: *"Ground truth is invisible to the agent. `correct_action` lives
in `chaos/faults.py`; nothing under `oncall/` may import from `chaos/`."*

Kyun itna sakht? Kyunki is file mein likha hai ki **har fault ka sahi jawaab kya
hai**. Agar `oncall/` ka koi bhi module isko import kar le — galti se bhi, ek
type hint ke liye bhi — toh agent ke paas answer key aa jaati hai. Phir "agent ne
sahi action choose kiya" ek measurement nahi, ek tautology ban jaata hai.

Ye rule grep se check hota hai, bharose pe nahi:

    grep -rn "from chaos\\|import chaos" oncall/     # khaali rehna chahiye
    grep -rn "correct_action\\|is_relevant" oncall/  # khaali rehna chahiye

Ye dono `make lint` ka hissa hain (`docs/DECISIONS.md` D31 — linter ke paas koi
escape hatch nahi hai).

─────────────────────────────────────────────────────────────────────────────
🔴 Do cheezein jo yahan hain aur kahin nahi
─────────────────────────────────────────────────────────────────────────────
1. `Fault.correct_action` — scoring ke liye. `scripts/eval_harness.py` (Phase 8)
   isko padhta hai, agent nahi.
2. `Fault.relevant_runbooks` — retrieval scoring ke liye (N2 metric). Kaunsa
   runbook is fault ke liye "relevant" hai, wo ek **insaan ka judgement** hai,
   aur wo judgement yahan likha hua hai — retrieval ke code mein nahi.

─────────────────────────────────────────────────────────────────────────────
🔴 Break aur heal, dono ek hi jagah — jaan-boojh kar
─────────────────────────────────────────────────────────────────────────────
Har fault ka `break_fn` aur `heal_fn` isi file mein hai, us fault ke bilkul
paas. Alternative tha: `breakers.py` + `healers.py` (do files, verb ke hisaab se
bant kar). Wo reject kiya kyunki fault debug karte waqt sawaal hota hai *"F4
kaise tootta aur kaise theek hota hai"* — ek fault, do file khol kar padhna.
Yahan ek fault = ek jagah.

⚠️ **Chaos ke liye `exec_run` allowed hai.** `CLAUDE.md` §3 ka "no shell" rule
**action path** pe hai (`oncall/` ke andar). Chaos attacker hai, agent nahi —
aur asli duniya mein bhi failure "structured API" se nahi aati. Phir bhi hum
list-form args use karte hain (`["dd", "if=..."]`), string nahi — ek habit ke
taur pe.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # sirf type checker ke liye — runtime pe ye import nahi hota
    from docker.models.containers import Container


class _NoDockerSDK(Exception):
    """Placeholder exception jab docker SDK install na ho.

    🔴 Iska kaam sirf itna hai ki `except NotFound:` line COMPILE ho jaaye jab
       asli `NotFound` maujood na ho. Ek naya class isliye — `NotFound = Exception`
       likhna aasaan tha lekin galat: phir `except NotFound` **sab kuch** pakad
       leta, including "docker install nahi hai" wala RuntimeError, aur user ko
       "container nahi mila" dikhta jabki asli problem doosri thi.
       Ek galat error message ek missing error message se bura hai.
    """


# 🔴 Docker SDK ka import JAAN-BOOJH KAR optional hai — `oncall/config.py` ke
#    dotenv wale pattern ki tarah, aur usi wajah se.
#
#    Is file mein DO alag cheezein hain:
#      1. REGISTRY — 5 `Fault` dataclasses, ground truth, aur `_check_registry()`
#         ka import-time invariant guard. Isme Docker ka koi kaam nahi.
#      2. break/heal FUNCTIONS — inhe Docker chahiye.
#
#    Agar import module-level hard hota, toh `python -c "import chaos.faults"`
#    bina `pip install docker` fail karta — aur uske saath wo guard bhi jo poore
#    project ki thesis ki nigrani karta hai (*"F4 ka load_dependent True hona
#    chahiye, warna N1 measure karne layak nahi bachta"*). Yaani jo check sabse
#    zaruri hai, wo sabse der se chalta — install ke baad. Ye ulta hai.
#
#    Iska seedha faayda: `python -m chaos.inject --list` aur `--dry-run` aaj,
#    bina venv, chalte hain. Asli injection pe `_client()` ek saaf message deta
#    hai, ek raw `ModuleNotFoundError` traceback nahi.
try:
    import docker
    from docker.errors import NotFound
except ModuleNotFoundError:  # pragma: no cover - install state pe depend karta hai
    docker = None  # type: ignore[assignment]
    NotFound = _NoDockerSDK  # type: ignore[misc,assignment]

# ═════════════════════════════════════════════════════════════════════════════
# 0. Container naam — `docker-compose.yml` se, hardcoded
# ═════════════════════════════════════════════════════════════════════════════
#
# 🔴 Ye naam `docker-compose.yml` ke `container_name:` se copy kiye gaye hain
#    (lines 42, 107, 139). Compose default naam `<project>-<service>-1` hota hai
#    jo folder ke naam pe depend karta hai — aur folder ka naam badalna hai
#    (`SETU` → `oncall`, CLAUDE.md §7). Isliye compose mein `container_name:`
#    explicitly likha gaya, aur yahan wahi naam.
SHOPAPP = "oncall-shopapp"
SHOPDB = "oncall-shopdb"
SHOPCACHE = "oncall-shopcache"

#: 🔴 Agent ka DB. Chaos isko kabhi touch nahi karta — agar audit trail hi mar
#: jaaye toh experiment ka koi record nahi bachta.
ONCALL_DB = "oncall-db"

#: F2 ka target. `docker-compose.yml` mein ye ek 16 MB tmpfs hai — bounded,
#: taaki 8 GB Mac pe ek bhaga hua `dd` poori RAM na kha jaaye.
CACHE_DIR = "/tmp/shopapp-cache"

#: `Fault.touches` ki spelling check karne ke liye. 🔴 `ONCALL_DB` jaan-boojh kar
#: is set mein NAHI hai: chaos ko agent ka apna DB chhune ka koi haq nahi, aur
#: agar kabhi kisi fault ke `touches` mein wo naam aa jaaye toh import hi fail ho.
#: `frozenset` isliye ki ye ek allowlist hai — allowlist mutable nahi honi chahiye
#: (wahi soch `oncall/actions/allowlist.py` mein).
_KNOWN_CONTAINERS = frozenset({SHOPAPP, SHOPDB, SHOPCACHE})


# ═════════════════════════════════════════════════════════════════════════════
# 1. Docker helpers — chhote, taaki har fault ki body padhne layak rahe
# ═════════════════════════════════════════════════════════════════════════════


def _client() -> docker.DockerClient:
    """Docker daemon se connect. `from_env()` `DOCKER_HOST` ya default socket padhta hai.

    ⚠️ Agar Docker Desktop band hai toh ye `DockerException` phenkta hai. Hum
    isko pakadte NAHI — chaos ka caller ek insaan hai (CLI), aur usko asli error
    dikhna chahiye, "kuch galat ho gaya" nahi.
    """
    if docker is None:
        raise RuntimeError(
            "docker SDK install nahi hai. Setup:\n"
            "  make venv && make install\n"
            "  source .venv/bin/activate\n"
            "(Registry, `--list` aur `--dry-run` bina iske chalte hain; "
            "asli injection nahi.)"
        )
    return docker.from_env()


def _get(name: str) -> Container:
    """Naam se container. `NotFound` ko ek padhne layak message mein badalta hai."""
    # 🔴 `_client()` `try` ke BAAHAR hai, jaan-boojh kar. Agar wo andar hota aur
    #    docker SDK missing hota, toh uska RuntimeError is `except` mein aa kar
    #    "container nahi mila" ban jaata — ek jhootha diagnosis. Sirf wahi
    #    exception pakdo jiska matlab tumhe pata hai.
    client = _client()
    try:
        return client.containers.get(name)
    except NotFound:
        raise RuntimeError(
            f"container '{name}' nahi mila. `make up` chalaya? "
            f"`docker ps -a --format '{{{{.Names}}}}'` se check karo."
        ) from None


def _exec(name: str, cmd: list[str], *, detach: bool = False) -> tuple[int | None, str]:
    """Container ke andar ek command. 🔴 `cmd` ek LIST hai, string nahi.

    List form matlab koi shell involved nahi — `dd if=/dev/zero of=x; rm -rf /`
    jaisa kuch ek hi argument ban jaata hai, do commands nahi. Chaos ke liye ye
    zaruri nahi tha (chaos ka input hardcoded hai), lekin ye wahi habit hai jo
    `oncall/actions/handlers.py` mein **compulsory** hai — do jagah do style
    rakhne se galti aati hai.

    Args:
        detach: `True` matlab wapas mat ruko. F3 ke liye zaruri — wo command
            600 second tak ek lock hold karti hai, aur uska wait karna matlab
            inject CLI 10 minute hang.
    """
    res = _get(name).exec_run(cmd, detach=detach)
    if detach:
        return None, ""
    out = res.output.decode("utf-8", errors="replace") if res.output else ""
    return res.exit_code, out


def _psql(sql: str, *, detach: bool = False) -> tuple[int | None, str]:
    """shopdb ke andar `psql -c`. Chaos DB ko seedha maar sakta hai — agent nahi.

    🔴 `-v ON_ERROR_STOP=1` — bina iske psql pehli error ke baad bhi chalta rehta
       hai aur exit code 0 deta hai. Yaani ek toothi hui chaos injection "success"
       report karti, aur hum ek fault measure karte jo actually inject hua hi
       nahi. Ye ek jhoothi green light hai — usi class ki galti jiske baare mein
       `CLAUDE.md` §2 kehta hai.
    """
    return _exec(
        SHOPDB,
        ["psql", "-v", "ON_ERROR_STOP=1", "-U", "shop", "-d", "shop", "-c", sql],
        detach=detach,
    )


# ═════════════════════════════════════════════════════════════════════════════
# 2. Fault — ek dataclass jo "kya toota" aur "sahi jawaab kya tha" dono rakhta hai
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Fault:
    """Ek fault class. `frozen=True` — inject hone ke baad definition badal nahi sakti.

    🔴 Agar ye mutable hota toh ek run ke beech mein `correct_action` badla ja
       sakta tha, aur scoring ka koi matlab nahi rehta. Ek experiment ki ground
       truth measurement se pehle tay hoti hai, baad mein nahi.
    """

    id: str  #: "F1" … "F5". Ye string `incidents.fault_id` mein jaati hai.
    title: str  #: ek line, insaan ke liye
    symptom: str  #: `/health` kya bolega — detector ka nazariya
    why_restart_fails: str  #: 🔥 F1 ke alawa sab ke liye ye khaali nahi hai

    #: 🔴 GROUND TRUTH. Agent ko kabhi nahi dikhta. Sirf `scripts/eval_harness.py`
    #: (Phase 8) isko padhta hai, verdict aane ke BAAD.
    correct_action: str

    #: 🔴 Sahi TARGET. F5 ka sabak: verb se zyada target maayne rakhta hai.
    #: `""` matlab is fault mein target ka koi matlab nahi (koi aisa fault abhi
    #: nahi hai, lekin field khaali chhodne layak hai — misaal ke liye ek
    #: `escalate`-only fault).
    correct_target: str = ""

    #: ⚠️ Wo actions jo "galat nahi" hain. Misaal: F5 mein shopcache band hai;
    #: `start_service("shopcache")` sahi hai, lekin `restart_service("shopcache")`
    #: bhi kaam karti hai (Docker ka restart ek stopped container ko start karta
    #: hai). In dono ko "galat" ginna scoring ko jhootha bana dega.
    #:
    #: 🔥 Asli sawaal action ka VERB nahi, TARGET hai — F5 mein agent ne
    #: shopcache choose kiya ya shopapp? Yahi localization hai (`docs/PRD.md` F5).
    also_acceptable: tuple[str, ...] = ()

    #: N2 (retrieval) scoring. Kaunse runbook is fault ke liye relevant hain —
    #: ye ek insaan ka faisla hai, algorithm ka nahi.
    relevant_runbooks: tuple[str, ...] = ()

    #: Restart akela kaam karta hai? Sirf F1 pe `True`. Ye field N1 ka setup hai.
    restart_alone_fixes: bool = False

    #: 🔥 F4 ka wo flag jiske liye poora project bana hai: idle pe health green,
    #: load pe red. `naive_recovered=True, sustained_recovered=False`.
    load_dependent: bool = False

    #: Kaunse container ye fault (break YA heal ke dauraan) chhuta hai. 🔴 Sirf
    #: `chaos/inject.py` ke dry-run print ke liye — "--write ke bina kuch nahi
    #: hota" ka waada tabhi bharosemand hai jab dry-run SACH batata ho ki kya
    #: hota. Ye `correct_target` se derive NAHI ho sakta: F4 ka target shopdb
    #: hai lekin uska heal shopapp ko bhi band karta hai (`_heal_f4` dekho).
    #: `_check_registry()` verify karta hai ki har naam ek asli container hai.
    #: ⚠️ Dhyaan do: yahan **full container naam** hai (`oncall-shopapp`), jabki
    #: `correct_target` mein **short service naam** (`shopapp`). Ye inconsistency
    #: nahi, do alag vocabulary hain: `touches` Docker se baat karta hai,
    #: `correct_target` allowlist se (`ALLOWED_SERVICES` = short naam,
    #: `oncall/actions/allowlist.py:104`). Inhe "theek" karne ki koshish mat karna.
    touches: tuple[str, ...] = ()

    break_fn: Callable[[], str] = field(repr=False, default=lambda: "noop")
    heal_fn: Callable[[], str] = field(repr=False, default=lambda: "noop")


# ═════════════════════════════════════════════════════════════════════════════
# 3. F1 — process crash. Baseline. Restart se theek HOTA hai.
# ═════════════════════════════════════════════════════════════════════════════
#
# 🔴 Ye fault jaan-boojh kar "aasaan" hai. Ye POSITIVE CONTROL hai: agar agent
#    F1 bhi fix na kar paaye, toh baaki chaar ke result ka koi matlab nahi —
#    kharaabi agent mein hai, fault mein nahi. Isliye F1 pe fail hona ek
#    **infrastructure bug** hai, ek interesting finding nahi.


def _break_f1() -> str:
    """shopapp ko SIGKILL. Container exit 137 pe mar jaata hai.

    🔴 Kyun `container.kill()` aur andar se `kill -9 1` nahi?
       Linux kernel PID namespace ke init (PID 1) ko usi namespace se aane wale
       un signals se bachata hai jinke liye handler nahi hai — aur SIGKILL ka
       handler ho hi nahi sakta. Yaani `docker exec shopapp kill -9 1` **chup-chaap
       kuch nahi karta**. Ye ek aisi galti hai jo "chal gaya" dikhti hai: exit
       code 0, aur container zinda. Bahar se (Docker daemon = doosra namespace)
       SIGKILL kaam karta hai.

    ⚠️ Ye `docker-compose.yml` ke `restart: "no"` pe depend karta hai. Agar wahan
       `restart: unless-stopped` hota, Docker khud container wapas utha leta aur
       fault 2 second mein gayab ho jaata — agent ke paas fix karne ko kuch nahi
       bachta. **Ye ek hidden dependency hai** (`docs/DECISIONS.md` D29 usi class
       ki galti hai).
    """
    try:
        _get(SHOPAPP).kill(signal="SIGKILL")
    except Exception as exc:  # noqa: BLE001
        # 🔴 Idempotent inject (2026-08-24): pichhla break already-exited
        #    chhoda ho aur koi heal na hua ho, dobara kill Docker 409 deta
        #    tha -> poora inject crash (incident row tak nahi banti). Fault
        #    DESIRED state hai, action nahi — already-down = goal achieved.
        if "not running" in str(exc) or "Conflict" in type(exc).__name__:
            return f"{SHOPAPP} pehle se band tha — F4/F1 state already active"
        raise
    return f"{SHOPAPP} ko SIGKILL bheja — container ab 'exited' state mein hai"


def _heal_f1() -> str:
    _get(SHOPAPP).restart(timeout=5)
    return f"{SHOPAPP} restart ho gaya"


# ═════════════════════════════════════════════════════════════════════════════
# 4. F2 — disk full. Restart disk khaali nahi karta.
# ═════════════════════════════════════════════════════════════════════════════
#
# 🔴 Ye fault ek DESIGN HOLE theek karta hai jo maine khud banaya tha.
#    `docs/PRD.md` F2 kehta tha "disk full — /tmp bhar do". Lekin `stack/shopapp/
#    app.py` ka `/health` sirf DB aur Redis check karta tha — disk ko chhoota hi
#    nahi. Yaani F2 inject hota, aur `/health` **200 hi rehta**. Detector kabhi
#    incident hi na kholta. Ek fault jo detect nahi hota, wo fault nahi hai.
#
#    Isliye is change mein `/health` mein ek teesra check add hua: cache
#    directory likhne layak hai ya nahi. Ye realistic hai (asli services scratch
#    space check karti hain) aur `clear_directory` action ko ek asli asar deta hai.


def _break_f2() -> str:
    """tmpfs ko 100% bhar do.

    🔴 `docker-compose.yml` mein `/tmp/shopapp-cache` ek **16 MB tmpfs** hai.
       tmpfs RAM-backed hoti hai — bina `size=` limit ke ek `dd` 8 GB Mac ki
       poori RAM kha sakta tha. Bounded tmpfs ka matlab: fault asli hai (writes
       `ENOSPC` se fail hoti hain) lekin blast radius 16 MB.

    `count=64` (64 MB) 16 MB tmpfs pe jaan-boojh kar zyada hai — `dd` beech mein
    fail hoga aur exit code non-zero dega. **Wahi success hai.** Isliye hum exit
    code check nahi karte; hum baad mein "koi jagah bachi?" check karte hain.
    """
    _exec(
        SHOPAPP,
        ["dd", "if=/dev/zero", f"of={CACHE_DIR}/chaos-fill.bin", "bs=1M", "count=64"],
    )
    _, out = _exec(SHOPAPP, ["df", "-h", CACHE_DIR])
    return f"{CACHE_DIR} bhar diya (tmpfs 16 MB). df:\n{out.strip()}"


def _heal_f2() -> str:
    """🔴 `-mindepth 1` — directory KHUD delete na ho, sirf uska content jaaye.

    Bina `-mindepth 1`, `find /tmp/shopapp-cache -delete` mount point ko hi
    hata deta (ya `EBUSY` deta) aur phir har write `ENOENT` se fail hoti — fault
    theek hone ki jagah **badal** jaata. Yahi flag `handlers.py` ke
    `clear_directory()` mein bhi hai, usi wajah se.
    """
    _exec(SHOPAPP, ["find", CACHE_DIR, "-mindepth", "1", "-delete"])
    return f"{CACHE_DIR} khaali kar diya"


# ═════════════════════════════════════════════════════════════════════════════
# 5. F3 — DB lock. shopapp restart karne se kuch nahi hota: kharaabi shopdb mein hai.
# ═════════════════════════════════════════════════════════════════════════════
#
# 🔴 Ye fault LOCALIZATION + LAYER dono test karta hai. `/health` 503 deta hai
#    aur error `shopapp` ke logs mein aata hai — lekin lock `shopdb` ke andar ek
#    doosre session ne pakda hua hai. shopapp ko 100 baar restart karo, lock
#    wahin rahega.
#
# 🔴 Evidence ka farq (isi liye `probe_once()` mein `type(exc).__name__` bachaya
#    jaata hai): F1 mein `ConnectError` aata hai (TCP hi nahi juda). F3 mein
#    connection ban jaata hai, query **latak** jaati hai → `PoolTimeout` /
#    `QueryCanceled`. LLM #1 ko yahi farq batata hai ki kharaabi network mein
#    nahi, DB ke andar hai.


def _break_f3() -> str:
    """`orders` table pe ACCESS EXCLUSIVE lock, 600 second ke liye.

    🔴 `ACCESS EXCLUSIVE` sabse sakht lock mode hai — ye plain `SELECT` ko bhi
       block karta hai. `/health` `SELECT count(*) FROM orders` chalata hai
       (`stack/shopapp/app.py`), toh health probe 3 s timeout pe latak kar 503
       degi. Halka lock (`ROW EXCLUSIVE`) SELECT ko block nahi karta — fault
       invisible ho jaata.

    🔴 `detach=True` zaruri hai. `psql -c` poori string ko EK transaction ki tarah
       bhejta hai, toh `pg_sleep(600)` tak lock hold rehta hai. Bina detach,
       inject CLI khud 10 minute block ho jaati.

    ⚠️ 600 s ka matlab: fault khud-ba-khud 10 minute mein gayab ho jaata hai. Ye
       ek **hidden clock dependency** hai (D29). Agar ek incident 10 minute se
       zyada le, fault bina fix hone bhi "theek" dikhega aur oracle ✅ bol dega —
       ek jhootha pass. Tripwire: `docs/RISKS.md` mein iska record hai; agar
       koi incident 10 min se lamba chale toh ye number badhana padega.
    """
    _psql(
        "BEGIN; LOCK TABLE orders IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(600);",
        detach=True,
    )
    time.sleep(1.0)  # lock lagne ka waqt do, warna heal/verify race karega
    return "orders table pe ACCESS EXCLUSIVE lock lag gaya (600 s tak)"


def _heal_f3() -> str:
    """Lock hold karne wale backend ko terminate karo.

    🔴 `pid <> pg_backend_pid()` — apne aap ko maar dena sabse aasaan bug hai.
       Bina iske ye query khud ko terminate kar sakti hai aur lock zinda reh
       jaata.
    """
    _psql(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE datname = 'shop' AND pid <> pg_backend_pid() "
        "AND query LIKE '%pg_sleep%';"
    )
    return "lock hold karne wale backend terminate kar diye"


# ═════════════════════════════════════════════════════════════════════════════
# 6. 🔥 F4 — pool exhaustion. POORE PROJECT KA HEADLINE.
# ═════════════════════════════════════════════════════════════════════════════
#
# Ye wo ek fault hai jiske liye ye project exist karta hai. Mechanism poora
# samajhna zaruri hai — interviewer yahi poochega.
#
# 1. `ALTER SYSTEM SET max_connections = 5` → ye value `postgresql.auto.conf`
#    FILE mein likhi jaati hai. 🔥 Yaani ye **restart ke baad bhi zinda rehti
#    hai**. Ek plain `restart_service` isko theek NAHI karta.
# 2. shopdb restart → nayi limit lagoo. Ab Postgres sirf 5 connection deta hai
#    (aur normally usme se 3 `superuser_reserved_connections` ke liye reserve
#    hote — lekin shopapp khud superuser hai, neeche dekho).
# 3. shopapp ka pool `min_size=1` hai (`stack/shopapp/app.py` POOL_MIN=1). Idle
#    mein use ek hi connection chahiye → `/health` **200 deta hai**.
# 4. Load aaya → pool `max_size=10` (POOL_MAX) ki taraf badhta hai → 6th
#    connection pe Postgres `FATAL: sorry, too many clients already` phenkta
#    hai → `/health` 503.
#
# ⚠️ shopapp ko jo EXCEPTION milta hai wo `TooManyConnections` nahi, `PoolTimeout`
#    hai — psycopg_pool waiting client ko queue karta hai aur 3 s baad haar
#    maanta hai. Asli `FATAL` string do jagah hai: shopdb ke logs mein, aur
#    shopapp ke logs mein `psycopg.pool` ke WARNING ke through. Dono `docker
#    logs` se aate hain — isi liye OnCall ka LLM #1 input container logs hai.
#
# 🔥 Isi liye naive oracle (ek probe, idle) ✅ bolti hai aur sustained oracle
#    (3 probes, 30 s, ~20 rps) ❌. Ek fault, ek action, do ulte verdict.
#    `Verdict.disagreed` (oncall/oracle/health.py) exactly yahi pakadta hai.
#
# 🔴 Sahi fix DO STEP hai: `set_db_setting('max_connections', 100)` **phir**
#    restart. LLM ka `restart_service` jawaab **galat nahi tha — adhoora tha.**
#    Ye distinction poore project ki thesis hai.

_F4_ORIGINAL_MAX_CONN = 100  #: `stack/oncalldb`/shopdb ka default; heal isko wapas laata hai

#: 🔴 Ye number **2 nahi** hai, aur ye ek asli bug fix hai.
#:
#: `docs/PRD.md` mein F4 "max_connections = 2" likha tha. Wo value Postgres ki
#: apni validity rule todti hai.
#:
#: 🔴 Version matter karta hai, aur pehli baar maine GALAT manual quote ki thi.
#: shopdb `postgres:15-alpine` hai (`docker-compose.yml`), toh PG15 ki docs
#: chahiye — PG16 ki nahi. PG16 mein ek naya parameter `reserved_connections`
#: hai jo **PG15 mein exist hi nahi karta**, aur mera pehla quote usi ka tha.
#:
#: PG15 ka asli text, `superuser_reserved_connections` ke baare mein:
#:     *"The default value is three connections. The value must be less than
#:      `max_connections`. This parameter can only be set at server start."*
#: (✅ verified 2026-08-22, postgresql.org/docs/15/runtime-config-connection.html)
#:
#: `max_connections=2` pe ye rule banta hai `3 < 2` — jo jhooth hai. PG15 mein
#: subtraction wali koi baat nahi hai, toh ye violation PG16 se bhi saaf hai.
#:
#: ⚠️ Us page pe ye NAHI likha ki violate karne pe server start hone se **mana
#: karta hai** — wo mera inference hai, docs ka statement nahi. Isliye ise ⚠️
#: mark kiya hai, ✅ nahi (`CLAUDE.md` §4: estimate ko measurement ki tarah pesh
#: mat karo). Lekin dono soorat mein `2` kharaab hai: ya server boot nahi karega,
#: ya wo config invalid hai. Aur agar server boot nahi karta toh F4 "subtle
#: load-dependent fault" ki jagah "DB gayab" ban jaata — naive probe bhi turant
#: fail karti aur naive-vs-sustained ka pura farq khatam.
#:
#: Verify karne ka tareeka (Pranay ke liye, jab Docker chal raha ho):
#:     docker compose exec shopdb psql -U shop -d shop \
#:       -c "ALTER SYSTEM SET max_connections = 2" \
#:       && docker compose restart shopdb && docker logs oncall-shopdb | tail -20
#: Agar wo boot nahi karta, ⚠️ ✅ ban jaata hai aur ye comment update hota hai.
#:
#: 5 kyun: 5 > 3 → rule satisfied. Idle pe pool ko 1 connection chahiye
#: (POOL_MIN=1) → `/health` 200. Load pe pool 10 (POOL_MAX) ki taraf badhta hai →
#: 5 pe deewar → `FATAL: sorry, too many clients already`.
#:
#: 🔴 Ek zaruri detail: shopapp `shop` user se connect karta hai, aur postgres
#: image mein `POSTGRES_USER` **superuser** hota hai. Isliye
#: `superuser_reserved_connections` shopapp ko nahi rokta — wo poore 5 use kar
#: sakta hai. Agar shopapp non-superuser hota toh uski asli limit 5-3=2 hoti.
#: Tripwire: `make up` ke baad `docker logs oncall-shopdb` mein koi
#: `superuser_reserved_connections` error nahi hona chahiye.
_F4_BROKEN_MAX_CONN = 5


def _break_f4() -> str:
    """max_connections ko 5 kar do, phir shopdb restart.

    🔴 Kyun `ALTER SYSTEM` aur `SET`/`ALTER DATABASE` nahi?
       - `SET max_connections = 5` chalta hi nahi — ye ek postmaster-level
         parameter hai, session-level nahi.
       - `ALTER DATABASE ... SET` bhi is parameter pe allowed nahi.
       Sirf `ALTER SYSTEM` (yaani `postgresql.auto.conf` likhna) + restart kaam
       karta hai. Aur wahi is fault ko "restart-proof" banata hai — accident
       nahi, design.

    ⚠️ Bind parameters `ALTER SYSTEM` mein kaam nahi karte (ye ek utility
       statement hai, prepared statement nahi). Isliye value string interpolation
       se aati hai. Chaos mein ye theek hai — value ek module constant hai, user
       input nahi. 🔒 `oncall/actions/handlers.py` mein yahi jagah ek injection
       surface hai, aur wahan value pehle int-cast + range-check hoti hai
       (`docs/RISKS.md` R5).
    """
    _psql(f"ALTER SYSTEM SET max_connections = {_F4_BROKEN_MAX_CONN};")
    _get(SHOPDB).restart(timeout=10)
    time.sleep(3.0)  # Postgres ko wapas accept karne ka waqt do
    return (
        f"shopdb pe max_connections={_F4_BROKEN_MAX_CONN} "
        "(postgresql.auto.conf mein likha gaya — restart-proof), shopdb restart ho gaya"
    )


def _heal_f4() -> str:
    """🔴 shopapp PEHLE band karo, phir DB theek karo.

    Wajah: max_connections chhoti hai, aur shopapp ka pool un thodi si slots ko
    pakad kar baitha hai. Agar hum seedha `psql` maarein, wo `too many clients`
    se fail ho sakta hai — yaani heal khud us fault ka shikaar ho jaata jise wo
    theek karne aaya hai. shopapp band = slots khaali = heal deterministic.
    """
    _get(SHOPAPP).stop(timeout=5)
    _psql(f"ALTER SYSTEM SET max_connections = {_F4_ORIGINAL_MAX_CONN};")
    _get(SHOPDB).restart(timeout=10)
    time.sleep(3.0)
    _get(SHOPAPP).start()
    return f"max_connections wapas {_F4_ORIGINAL_MAX_CONN}, shopdb+shopapp restart"


# ═════════════════════════════════════════════════════════════════════════════
# 7. F5 — dependency down. Jo service FAIL dikhti hai, wo galat service hai.
# ═════════════════════════════════════════════════════════════════════════════
#
# 🔥 Ye fault LOCALIZATION test karta hai, aur ye sabse aasaani se galat hone
#    wala fault hai. Symptom shopapp pe dikhta hai: `/health` 503, aur error
#    shopapp ke logs mein. Lekin shopapp bilkul theek hai — shopcache band hai.
#
#    Ek naive agent (aur bahut se insaan) `restart_service("shopapp")` bolte
#    hain, kyunki wahi service red dikh rahi hai. Restart hota hai, aur health
#    phir 503 — kyunki asli kharaabi ek doosre container mein hai.
#
# 🔴 Yahan "sahi verb" ka sawaal nahi hai. `start_service("shopcache")` sahi hai
#    aur `restart_service("shopcache")` bhi kaam kar jaata hai (Docker ka restart
#    ek stopped container ko start karta hai). Scoring ka asli sawaal:
#    **agent ne shopcache choose kiya ya shopapp?** Isliye `also_acceptable`.


def _break_f5() -> str:
    """shopcache ko band kar do. shopapp zinda rehta hai aur 503 dena shuru karta hai.

    🔴 `stop()` aur `kill()` nahi. Farq: `stop` SIGTERM bhejta hai (graceful),
       `kill` SIGKILL. Redis ke liye graceful shutdown zyada realistic hai — asli
       duniya mein cache node deploy ya OOM-eviction se jaata hai, aksar cleanly.
       Aur `stop` ke baad container "exited (0)" dikhta hai — logs saaf rehte
       hain, jo LLM #1 ke liye behtar evidence hai.
    """
    _get(SHOPCACHE).stop(timeout=5)
    return f"{SHOPCACHE} band kar diya — shopapp zinda hai lekin uska redis ping fail karega"


def _heal_f5() -> str:
    _get(SHOPCACHE).start()
    time.sleep(1.0)
    return f"{SHOPCACHE} start ho gaya"


# ═════════════════════════════════════════════════════════════════════════════
# 8. 🔴 Registry — GROUND TRUTH. Agent isko kabhi nahi dekhta.
# ═════════════════════════════════════════════════════════════════════════════

FAULTS: dict[str, Fault] = {
    "F1": Fault(
        id="F1",
        title="Process crash — shopapp ko SIGKILL",
        symptom="/health se connection refused (ConnectError). Container 'exited (137)'.",
        why_restart_fails="",  # 🔴 khaali — F1 pe restart kaam KARTA hai
        correct_action="restart_service",
        correct_target="shopapp",
        also_acceptable=("start_service",),
        relevant_runbooks=("rb-docker-container-exited",),
        restart_alone_fixes=True,
        touches=(SHOPAPP,),
        break_fn=_break_f1,
        heal_fn=_heal_f1,
    ),
    "F2": Fault(
        id="F2",
        title="Disk full — cache tmpfs 100% bhar gaya",
        symptom="/health 503, checks.disk = 'FAIL OSError: [Errno 28] No space left on device'.",
        why_restart_fails="Restart se disk khaali nahi hoti. Naya process usi bhari tmpfs pe likhega.",
        correct_action="clear_directory",
        correct_target="shopapp",
        also_acceptable=(),
        relevant_runbooks=("rb-disk-full-cache-dir",),
        touches=(SHOPAPP,),
        break_fn=_break_f2,
        heal_fn=_heal_f2,
    ),
    "F3": Fault(
        id="F3",
        title="DB lock — orders table pe ACCESS EXCLUSIVE lock",
        symptom="/health 503, DB check 3 s timeout pe latak kar fail. ReadTimeout/PoolTimeout, ConnectError nahi.",
        why_restart_fails="Lock shopdb ke andar ek doosre session ne pakda hai. shopapp restart karne se wo session nahi marta.",
        correct_action="kill_blocking_queries",
        correct_target="shopdb",
        also_acceptable=(),
        relevant_runbooks=("rb-postgres-lock-contention",),
        touches=(SHOPDB,),
        break_fn=_break_f3,
        heal_fn=_heal_f3,
    ),
    "F4": Fault(
        id="F4",
        title="🔥 Pool exhaustion — max_connections=5, ALTER SYSTEM se persist",
        symptom="Idle pe /health 200. Load pe 'FATAL: sorry, too many clients already' → 503.",
        why_restart_fails=(
            "ALTER SYSTEM postgresql.auto.conf mein likhta hai — value restart ke baad bhi zinda "
            "rehti hai. Restart sirf pool khaali karta hai, aur idle pool ko 1 connection chahiye, "
            "toh ek akela probe 200 deta hai. Deewar (max_connections=5) wahin khadi hai aur load "
            "aane pe turant dikhti hai. Fault time-dependent NAHI, load-dependent hai."
        ),
        correct_action="set_db_setting",
        correct_target="shopdb",
        also_acceptable=(),
        relevant_runbooks=("rb-postgres-max-connections", "rb-postgres-lock-contention"),
        load_dependent=True,  # 🔥 N1 ka poora point
        # 🔴 Do naam, jabki correct_target sirf shopdb hai. Wajah `_heal_f4` mein
        #    likhi hai: heal shopapp ko pehle band karta hai, warna heal khud
        #    `too many clients` se mar jaata. Dry-run ko ye sach dikhana hai.
        touches=(SHOPDB, SHOPAPP),
        break_fn=_break_f4,
        heal_fn=_heal_f4,
    ),
    "F5": Fault(
        id="F5",
        title="Dependency down — shopcache band",
        symptom="/health 503, checks.redis = FAIL. shopapp khud theek hai.",
        why_restart_fails="Jo service red dikh rahi hai (shopapp) wo kharaab nahi hai. shopapp restart karne se shopcache start nahi hota.",
        correct_action="start_service",
        correct_target="shopcache",
        also_acceptable=("restart_service",),  # target sahi ho toh verb se farq nahi
        relevant_runbooks=("rb-redis-dependency-down", "rb-docker-container-exited"),
        touches=(SHOPCACHE,),
        break_fn=_break_f5,
        heal_fn=_heal_f5,
    ),
}


# ═════════════════════════════════════════════════════════════════════════════
# 9. Scoring API — sirf `scripts/eval_harness.py` ke liye
# ═════════════════════════════════════════════════════════════════════════════
#
# 🔴 In teen functions ka naam `oncall/` mein grep karne pe kabhi nahi milna
#    chahiye. `make lint` ka guard: `grep -rn "correct_action\|is_relevant" oncall/`


def is_relevant(fault_id: str, runbook_id: str) -> bool:
    """N2 (retrieval accuracy) ka judge. Human label, algorithm nahi.

    🔴 Ye function ek RAAY hai jo pehle likh di gayi hai. Iska poora point ye hai
       ki relevance ka faisla retrieval ke result dekhne ke BAAD na ho — warna
       hum apne hi score ko fit kar rahe honge. Isliye label yahan, chaos ke
       andar, measurement se pehle.
    """
    fault = FAULTS.get(fault_id)
    return bool(fault) and runbook_id in fault.relevant_runbooks


def score_action(fault_id: str, action: str, target: str = "") -> tuple[bool, str]:
    """Kya agent ka action sahi tha? `(sahi?, kyun)`.

    🔴 Do-level scoring, jaan-boojh kar:
       1. Verb `correct_action` ya `also_acceptable` mein hona chahiye.
       2. Target `correct_target` se match hona chahiye (agar declared hai).

    F5 exactly isi liye aisa hai: `restart_service("shopapp")` verb-level pe
    "plausible" hai lekin target galat hai → ye FAIL hai. `restart_service
    ("shopcache")` verb-level pe "second choice" hai lekin target sahi hai → ye
    PASS hai. **Localization verb se zyada zaruri hai.**
    """
    fault = FAULTS.get(fault_id)
    if fault is None:
        return False, f"unknown fault_id '{fault_id}'"

    allowed = (fault.correct_action, *fault.also_acceptable)
    if action not in allowed:
        return False, f"action '{action}' galat — expected {allowed}"

    if fault.correct_target and target != fault.correct_target:
        return (
            False,
            f"action '{action}' sahi tha lekin target '{target}' galat — "
            f"expected '{fault.correct_target}' (localization miss)",
        )
    return True, "correct"


def fault_ids() -> list[str]:
    """`["F1", ... "F5"]` — CLI aur harness ke iterate karne ke liye."""
    return list(FAULTS)


# ═════════════════════════════════════════════════════════════════════════════
# 10. Import-time invariants — galat registry ko chalne se pehle rok do
# ═════════════════════════════════════════════════════════════════════════════
#
# 🔴 Ye `assert` nahi hain. `python -O` assert ko hata deta hai, aur tab ye check
#    chup-chaap gayab ho jaate — wahi "jhoothi green light" wali class ki galti.
#    Isliye explicit `raise`.
#
# ⚠️ Ye chalte hain import pe, test pe nahi. Wajah: registry ek data structure
#    hai, aur uska galat hona ek **configuration bug** hai jise turant dikhna
#    chahiye — 3 minute baad `make test` mein nahi.


def _check_registry() -> None:
    if len(FAULTS) != 5:
        raise RuntimeError(f"5 fault expected, {len(FAULTS)} mile — PRD F1–F5 se mismatch")

    for fid, f in FAULTS.items():
        if f.id != fid:
            raise RuntimeError(f"registry key '{fid}' aur Fault.id '{f.id}' match nahi karte")
        if not f.correct_action:
            raise RuntimeError(f"{fid}: correct_action khaali hai — ground truth ke bina scoring nahi")
        if not f.relevant_runbooks:
            raise RuntimeError(f"{fid}: relevant_runbooks khaali hai — N2 measure nahi hoga")
        if f.correct_action in f.also_acceptable:
            raise RuntimeError(f"{fid}: correct_action also_acceptable mein dobara hai")
        if fid != "F1" and not f.why_restart_fails:
            raise RuntimeError(
                f"{fid}: why_restart_fails khaali hai. Sirf F1 (baseline) pe restart kaam karta hai; "
                f"baaki har fault ko ye batana padta hai ki restart kyun kaafi nahi"
            )
        if fid == "F1" and not f.restart_alone_fixes:
            raise RuntimeError("F1 positive control hai — restart_alone_fixes True hona chahiye")
        # `touches` `chaos/inject.py` ke dry-run print ka source hai. Ek typo'd
        # container naam wahan chup-chaap ek jhooti "ye chhua jaayega" line
        # banata — aur ek galat dry-run se dry-run na hona behtar hai.
        if not f.touches:
            raise RuntimeError(f"{fid}: touches khaali hai — dry-run kya print karega?")
        for name in f.touches:
            if name not in _KNOWN_CONTAINERS:
                raise RuntimeError(
                    f"{fid}: touches mein '{name}' hai jo koi asli container nahi. "
                    f"Allowed: {sorted(_KNOWN_CONTAINERS)}"
                )

    # 🔥 Ye check poore project ki thesis ka guard hai. Agar koi F4 ka
    #    `load_dependent` galti se False kar de, N1 measure karne layak rehta hi
    #    nahi — aur ye chup-chaap hota. Isliye ye ek loud failure hai.
    if not FAULTS["F4"].load_dependent:
        raise RuntimeError(
            "F4 load_dependent=False hai. F4 hi wo fault hai jahan naive aur sustained "
            "oracle alag jawaab dete hain (N1). Iske bina project ka headline result nahi bachta."
        )
    if sum(1 for f in FAULTS.values() if f.restart_alone_fixes) != 1:
        raise RuntimeError("theek ek fault (F1) restart se fix hona chahiye — baseline ek hi hai")


_check_registry()
