"""Broker — 5 layers. Koi bhi action Docker tak isi file se hokar pahunchta hai.

🔒 Ye repo ka **enforcement point** hai. `allowlist.py` authority hai (kya legal
hai), aur ye file wo authority **lagati** hai. Dono ka farq matter karta hai:
allowlist ek list hai, broker ek darwaza.

────────────────────────────────────────────────────────────────────────────
"Broker" ka matlab kya (CLAUDE.md §1 — pehle concept, phir code)
────────────────────────────────────────────────────────────────────────────

Broker = beech mein khada wo aadmi jo dono taraf ko ek dusre se seedha baat
karne nahi deta. Yahan do taraf hain:

    LLM (untrusted, kuch bhi propose kar sakta hai)
        ↓
    BROKER  ← ye file
        ↓
    Docker (root-equivalent, jo bola wo kar dega)

Docker daemon ka socket root-jaisa power deta hai (THREAT_MODEL.md §2). Wo
socket "kya ye action samajhdaar hai?" nahi poochhta — wo bas kar deta hai.
Toh sochne ka kaam kisi ko karna padega, aur wo jagah yahi hai.

────────────────────────────────────────────────────────────────────────────
🔴 5 layers kyun, ek `if` kyun nahi
────────────────────────────────────────────────────────────────────────────

Ek badi `if` condition likhi ja sakti thi. Do wajah se nahi likhi:

1. **Har layer ek alag sawaal poochhti hai**, aur unke jawab alag jagah se
   aate hain. Layer 1–3 sirf `(action, params)` dekhti hain — inhe DB ki koi
   zarurat nahi. Layer 4 ko approvals table ki row chahiye. Layer 5 ko
   incident ka attempt count chahiye. Ek `if` mein ye teen bilkul alag
   information sources ghul-mil jaate, aur test karna mushkil ho jaata.

2. **`RejectedError.layer` measurement hai, decoration nahi.** Adversarial
   test ye assert karta hai ki attack *kaunsi* layer pe mara. Agar sirf
   "rejected" pata ho, toh ek din layer 3 tut jaaye aur layer 1 usi input ko
   kisi doosri wajah se reject karti rahe — test green rahega aur hole
   khula rahega. Layer number wo farq dikhata hai.

────────────────────────────────────────────────────────────────────────────
🔥 TOCTOU — asli wajah ki validation DOBARA yahan hoti hai
────────────────────────────────────────────────────────────────────────────

TOCTOU = Time-Of-Check to Time-Of-Use. Poora concept ek line mein: **jo aapne
check kiya aur jo aapne use kiya, unke beech waqt guzarta hai, aur us waqt
mein cheez badal sakti hai.**

Roz ka example: aapne fridge kholi, dekha doodh hai, darwaza band kiya, phone
uthaya, 10 minute baad chai banane gaye — beech mein kisi ne doodh khatam kar
diya. Aapka "check" sahi tha. Aapka "use" phir bhi fail hua.

Yahan wo gap **jaan-boojh kar bada** hai, kyunki beech mein insaan hai:

    LLM propose  →  Pydantic validate  →  DB row  →  ⏳ INSAAN approve
                                                      (minute? ghante?)
                                                   →  worker uthata hai
                                                   →  BROKER  →  Docker

`llm/schema.py` ne parse-time pe validate kiya tha. Wo check **purana** ho
chuka hai jab tak execute hota hai. Beech mein kya badal sakta hai:

  - allowlist khud badal gayi (naya deploy, ek action hata diya gaya)
  - DB row edit ho gayi (jiske paas DB access hai, ya SQL injection kahin aur)
  - wahi incident dobara aa gaya, attempt count badh gaya

Isliye broker Pydantic pe bharosa **nahi** karta. Wo maanta hai ki input
kahin se bhi aa sakta hai — aur dobara, poora, execute ke waqt check karta hai.

🔴 Iska ek seedha natija: is file ka koi bhi function `ProposedAction` (Pydantic
   model) accept nahi karta. Sirf plain `str` + `Mapping`. Agar broker Pydantic
   type maangta, toh usi validation pe dobara bharosa ho jaata jo stale hai.

────────────────────────────────────────────────────────────────────────────
🔴 Ye file DB se baat nahi karti, aur ye ek decision hai
────────────────────────────────────────────────────────────────────────────

Layer 4 ko approvals table ki row chahiye. Do tareeke thay:

  (a) Broker ko ek DB connection do, wo khud `SELECT` kar le.
  (b) Caller row padh kar laaye, aur broker ko **plain data** de.

(b) chuna. Teen wajah:

  1. **Test.** (a) mein broker ka test ek chalte Postgres ke bina nahi chal
     sakta. Jo layer sabse zyada adversarial test maangti hai, wo sabse
     mushkil-to-test ban jaati. (b) mein `tests/test_broker.py` ek frozen
     dataclass banata hai aur 20 attack cases 30 millisecond mein chalte hain.
  2. **Ek kaam, ek jagah.** SQL `oncall/db/queries.py` mein rehti hai. Broker
     ka kaam faisla hai, I/O nahi.
  3. **Lock ka sawaal broker ka nahi hai.** Row ko `FOR UPDATE` ke saath padhna
     hai ya nahi — wo worker ka transaction design hai. Agar broker khud
     `SELECT` karta, toh wo faisla chupchaap is file mein aa jaata.

⚠️ (b) ki keemat imaandaari se: broker ko jhoothi row di ja sakti hai. Lekin
   caller humara apna `worker.py` hai, LLM nahi. Trust boundary LLM ke saamne
   hai, apne worker ke saamne nahi — aur jo cheez har jagah shak karti hai, wo
   kahin bhi kaam nahi karti.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Mapping

from oncall.actions.allowlist import (
    ACTIONS,
    ALLOWED_DB_KEYS,
    ALLOWED_PATHS,
    ALLOWED_TABLES,
)
from oncall.config import MAX_ATTEMPTS

if TYPE_CHECKING:  # pragma: no cover - sirf type checker ke liye
    from oncall.actions.handlers import ActionResult


# ─────────────────────────────────────────────────────────────────────────────
# 🔴 `handlers` ka import yahan TOP pe JAAN-BOOJH KAR nahi hai
# ─────────────────────────────────────────────────────────────────────────────
#
# `handlers.py` `import docker` karta hai. Agar hum use module top pe import
# karein, toh `broker.py` bhi Docker SDK ke bina import nahi hoga. Natija:
# `tests/test_broker.py` — repo ka sabse security-critical test — sirf tab
# chalega jab Docker SDK installed ho.
#
# Wo backwards hai. Layer 1–5 mein Docker ka naam bhi nahi aata; wo sirf
# strings aur dicts compare karti hain. Ek check jo pure logic hai, use apni
# heaviest dependency ke peeche khada karna matlab wo check kam chalega —
# aur jo check kam chalta hai, wo sad jaata hai.
#
# Isliye `HANDLERS` sirf `execute()` ke andar import hota hai, us pal jab hum
# waqai container ko touch karne wale hain. `validate()` ko Docker chahiye hi
# nahi, aur wo bina kisi third-party package ke chalta hai.
#
# ⚠️ Cost imaandaari se: ek galat handler naam import-time pe nahi pakda jaata,
#    execute-time pe pakda jaata. Lekin wo drift already band hai —
#    `handlers.py:399` khud import-time pe `RuntimeError` phenkta hai agar
#    `HANDLERS` aur `ACTION_NAMES` match na karein. Do baar wahi cheez check
#    karne ki zarurat nahi.


class RejectedError(Exception):
    """Broker ne action mana kar diya. `layer` batata hai kaunsi ne.

    🔴 `layer` ek int hai, string nahi. Wajah: test `exc.layer == 3` assert
       karta hai. Agar message string hoti, test message ke wording pe tik
       jaata — aur ek typo fix karne se test toot jaata, jabki security
       behaviour wahi rehta. Test ko behaviour pe tikna chahiye, prose pe nahi.
    """

    def __init__(self, layer: int, reason: str) -> None:
        super().__init__(f"L{layer}: {reason}")
        self.layer = layer
        self.reason = reason


@dataclass(frozen=True)
class ApprovalRow:
    """`approvals` table ki ek row, plain data ke roop mein.

    Fields wahi hain jo `stack/oncalldb/init.sql:165` mein hain — na kam, na
    zyada. `rationale`, `created_at`, `decided_by` yahan nahi hain kyunki koi
    layer unpe faisla nahi karti, aur jo field faisla nahi badalti, wo security
    object mein rakhna sirf shor hai.

    🔴 `frozen=True` kyun: layer 4 row ko *compare* karta hai. Agar row mutable
       hoti, toh layer 4 pass hone ke baad, dispatch se pehle, koi usi object ko
       badal sakta tha — yaani TOCTOU hole ko band karne wali cheez mein hi
       TOCTOU hole. Frozen dataclass pe `row.action = "stop_service"` ek
       `FrozenInstanceError` hai.
    """

    incident_id: int
    attempt: int
    action: str
    params: Mapping[str, Any] = field(default_factory=dict)
    decision: str | None = None  #: 'approved' | 'rejected' | None (pending)


# ─────────────────────────────────────────────────────────────────────────────
# Type coercion — do chhote helpers, dono ek asli trap ke liye
# ─────────────────────────────────────────────────────────────────────────────


def _as_str(layer: int, name: str, raw: object) -> str:
    """Value ko `str` maano ya reject karo. Koi `str()` cast nahi.

    🔴 `str(raw)` yahan galat hota. `str(["a"])` deta hai `"['a']"` — ek
       aisi string jo kabhi allowlist mein nahi milegi, toh reject hi hogi...
       *aaj*. Kal koi list-ish value allowlist se match kar gayi toh? Cast
       galat input ko chupchaap kisi doosre input mein badal deta hai. Reject
       karna galat input ko galat rehne deta hai — aur wahi chahiye.
    """
    if not isinstance(raw, str):
        raise RejectedError(layer, f"{name} must be a string, got {type(raw).__name__}")
    return raw


def _as_int(layer: int, name: str, raw: object) -> int:
    """Value ko `int` banao ya reject karo.

    🔴 `bool` ka check SABSE PEHLE hai, aur ye ek asli Python trap hai:
       Python mein `bool` `int` ki **subclass** hai. Toh `isinstance(True, int)`
       `True` deta hai, aur `int(True)` deta hai `1`. Bina is check ke,
       `{"value": true}` chupchaap `max_connections = 1` ban jaata — jo
       `ALLOWED_DB_KEYS` ke floor 10 se neeche hai, aur Postgres boot hi nahi
       karta. Ek JSON `true` se database mar jaana — bas ek missing check ki
       doori pe.

    ⚠️ String accept karte hain (`"200"` → `200`) jaan-boojh kar: LLM JSON mein
       number ko quote mein bhej dena aam galti hai, aur wo ek *typo* hai, attack
       nahi. Lekin `int("2.5")` aur `int("0x10")` dono `ValueError` hain — toh
       jo cheez saaf integer nahi hai, wo phir bhi reject hoti hai.
    """
    if isinstance(raw, bool):
        raise RejectedError(layer, f"{name} cannot be a bool (bool is a subclass of int)")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError:
            raise RejectedError(layer, f"{name}={raw!r} is not an integer") from None
    raise RejectedError(layer, f"{name} must be an integer, got {type(raw).__name__}")


# ═════════════════════════════════════════════════════════════════════════════
# LAYER 1 · Naam allowlist mein hai?
# ═════════════════════════════════════════════════════════════════════════════


def _layer1_name(action: object) -> str:
    """Sawaal: "ye naam humne allow kiya tha?"

    Sabse pehle kyun: baaki chaaron layers ko `spec` chahiye, aur `spec` sirf
    ek allowed naam se aata hai. Ek unknown naam pe `spec_for()` `KeyError`
    phenkta — aur `KeyError` ek *crash* hai, `RejectedError` ek *faisla*. Farq
    ye hai ki faisla DB mein log hota hai aur UI pe dikhta hai; crash ek
    stack trace hai jise koi nahi padhta.

    🔴 `isinstance(action, str)` check bhi yahan hai. `ACTIONS` ek dict hai, aur
       dict lookup kisi bhi hashable cheez pe chal jaata — `1`, `None`, ek
       tuple. Wo sab `False` denge (kyunki keys strings hain), lekin error
       message "1 allowlist mein nahi hai" hota, jo galat kahani sunata hai.
       Type ka sawaal aur membership ka sawaal alag hain.
    """
    if not isinstance(action, str):
        raise RejectedError(1, f"action must be a string, got {type(action).__name__}")
    if action not in ACTIONS:
        raise RejectedError(1, f"{action!r} is not in the allowlist")
    return action


# ═════════════════════════════════════════════════════════════════════════════
# LAYER 2 · Service legal hai? (blast radius ka sawaal)
# ═════════════════════════════════════════════════════════════════════════════


def _layer2_service(action: str, params: Mapping[str, Any]) -> None:
    """Sawaal: "ye action **kis cheez pe** chalega?"

    Ye apni layer isliye hai (params ke saath mila kar nahi) kyunki ye blast
    radius decide karti hai. `restart_service` ek naam hai; `restart_service`
    **kis pe** — usme farq hai `shopapp` restart karne aur `oncall-db` (agent ka
    apna brain) restart karne mein. `handlers.py:100` isi layer pe bharosa
    karta hai: "_get() ka KeyError matlab broker mein bug hai".

    🔴 `escalate` ka ulta case: uska `spec.services` khaali frozenset hai, matlab
       wo koi service touch nahi karta. Toh uske paas `service` param hona hi
       **galti** hai. Bina is branch ke, `escalate(service="shopdb")` chup-chaap
       nikal jaata (layer 3 bhi use pakadta hai, lekin layer 3 ka message
       "extra key" hota — jo chhupa deta hai ki asli baat blast radius thi).
    """
    spec = ACTIONS[action]

    if not spec.services:
        if "service" in params:
            raise RejectedError(2, f"{action} touches no service, yet a service param was sent")
        return

    if "service" not in params:
        raise RejectedError(2, f"{action} requires a service param, which is missing")

    service = _as_str(2, "service", params["service"])
    if service not in spec.services:
        raise RejectedError(
            2,
            f"{action} is not allowed on {service!r} (allowed: {sorted(spec.services)})",
        )


# ═════════════════════════════════════════════════════════════════════════════
# LAYER 3 · Params ka shape aur har value ka allowlist
# ═════════════════════════════════════════════════════════════════════════════

#: `escalate` ke `reason` ki max length. Cap kyun: reason DB mein jaata hai aur
#: UI pe dikhta hai. Bina cap, ek 2 MB ka reason ek row ko phula deta aur
#: approval screen ko browser mein hang kar deta. 1000 chars = kaafi hai.
_MAX_REASON_LEN = 1000


def _layer3_params(action: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """Sawaal: "bilkul wahi keys hain? aur har value allowlist mein hai?"

    Return validated kwargs — yahi dict aage handler ko `**kwargs` banega.

    🔴 Key set **exact** match hai (`==`), subset nahi. Extra key hi asli khatra
       hai, aur wajah `**kwargs` hai: `handler(**{"service": "shopapp",
       "force": True})` ko Python `TypeError` deta *aaj*, lekin jis din kisi
       handler mein ek naya optional param add hoga, us din wahi extra key ek
       asli argument ban jaayegi — jise kisi ne allowlist nahi kiya. Isliye
       extra key ek reject hai, `TypeError` ka bharosa nahi.

    🔴 Value-level allowlists (path, key, table) ek hi wajah se hain, aur wo
       wajah `handlers.py` mein likhi hai: `ALTER SYSTEM` aur `VACUUM` bind
       parameters accept nahi karte, toh `key` aur `table` ko SQL string mein
       interpolate karna majboori hai. Us line ko safe banane wali cheez yahi
       hai — value ek fixed set se aati hai, LLM se nahi.
    """
    spec = ACTIONS[action]
    expected = set(spec.params)
    got = set(params)

    if got != expected:
        missing = sorted(expected - got)
        extra = sorted(got - expected)
        bits = []
        if missing:
            bits.append(f"missing={missing}")
        if extra:
            bits.append(f"extra={extra}")
        raise RejectedError(3, f"{action} has wrong params: {', '.join(bits)}")

    out: dict[str, Any] = {}

    # `service` layer 2 mein already check ho chuki — yahan sirf carry forward.
    if "service" in expected:
        out["service"] = _as_str(3, "service", params["service"])

    # ── path ─────────────────────────────────────────────────────────────────
    # 🔴 Ye check `==` se hota hai (set membership), `startswith()` se nahi.
    #    `path.startswith("/var/log/shopapp")` likhna aasan tha aur ek hole
    #    hota: `/var/log/shopapp/../../lib/postgresql/data` bhi us prefix se
    #    shuru hota hai. Prefix matching mein `..` ka jawab dena padta
    #    (normalise karo, symlink resolve karo, phir compare) — aur us chain
    #    mein ek galti = data directory delete. Exact match mein `..` ka sawaal
    #    hi paida nahi hota: string do allowed strings mein se ek hai, ya nahi.
    if "path" in expected:
        path = _as_str(3, "path", params["path"])
        if path not in ALLOWED_PATHS:
            raise RejectedError(3, f"path {path!r} is not allowed (allowed: {sorted(ALLOWED_PATHS)})")
        out["path"] = path

    # ── key + value (set_db_setting) ─────────────────────────────────────────
    # Dono ek saath handle hote hain kyunki range `key` pe depend karti hai —
    # `max_connections` ka (10, 500) aur `work_mem` ka (64, 262144) alag hain.
    if "key" in expected:
        key = _as_str(3, "key", params["key"])
        if key not in ALLOWED_DB_KEYS:
            raise RejectedError(3, f"db key {key!r} is not allowed (allowed: {sorted(ALLOWED_DB_KEYS)})")
        lo, hi = ALLOWED_DB_KEYS[key]
        value = _as_int(3, "value", params["value"])
        if not lo <= value <= hi:
            raise RejectedError(3, f"{key}={value} is outside range [{lo}, {hi}]")
        out["key"] = key
        out["value"] = value

    # ── table ────────────────────────────────────────────────────────────────
    if "table" in expected:
        table = _as_str(3, "table", params["table"])
        if table not in ALLOWED_TABLES:
            raise RejectedError(3, f"table {table!r} is not allowed (allowed: {sorted(ALLOWED_TABLES)})")
        out["table"] = table

    # ── reason (escalate) ────────────────────────────────────────────────────
    # 🔴 `.strip()` ke baad check hota hai. `reason="   "` technically non-empty
    #    string hai, lekin insaan ke liye khaali hai — aur escalate ka poora
    #    point hi ye hai ki insaan ko padhne ke liye kuch mile.
    if "reason" in expected:
        reason = _as_str(3, "reason", params["reason"]).strip()
        if not reason:
            raise RejectedError(3, "reason is empty — escalate needs a reason")
        if len(reason) > _MAX_REASON_LEN:
            raise RejectedError(3, f"reason is {len(reason)} chars, max {_MAX_REASON_LEN}")
        out["reason"] = reason

    # 🔒 Paranoid closing check. Agar kal allowlist mein ek naya param add ho
    # (e.g. `signal`) aur upar uska branch likhna bhool jaayein, toh `out` mein
    # wo key nahi hoti — aur handler ko adhoore kwargs jaate. Ye line us bhoolne
    # ko ek saaf reject bana deti hai, silent TypeError ke bajaye.
    if set(out) != expected:  # pragma: no cover - sirf tab jab code drift ho
        raise RejectedError(3, f"broker has no check for {sorted(expected - set(out))}")

    return out


# ═════════════════════════════════════════════════════════════════════════════
# LAYER 4 · Insaan ne **yahi** action approve kiya tha?
# ═════════════════════════════════════════════════════════════════════════════


def _layer4_approval(
    action: str,
    validated: Mapping[str, Any],
    approval: ApprovalRow,
    incident_id: int,
    attempt: int,
) -> None:
    """Sawaal: "jo chalne wala hai, wahi approve hua tha?"

    🔥 Ye layer sabse important hai, aur uski wajah ye hai ki baaki chaar layers
    ek *aur* attacker se bachati hain (LLM), lekin ye layer **hamare khud ke
    system ke andar** ka gap band karti hai.

    Sochiye ek broker jo sirf `decision == 'approved'` check karta hai. Attack:

        LLM propose karta hai  : restart_service(shopapp)   ← nirdosh dikhta hai
        Insaan padhta hai      : "haan, app restart kar do"  → approve
        ⏳ row DB mein padi hai
        Kisi ne row edit ki    : action = stop_service
        Worker uthata hai      : decision == 'approved' ✅
        Broker chalata hai     : stop_service(shopapp)

    Insaan ne "approve" par click kiya tha, lekin **jis cheez pe** click kiya
    tha wo nahi chali. Approval gate maujood tha aur bekaar tha. Isliye layer 4
    ka check ye hai ki row ka action + params **wahi** hain jo abhi chalne wale
    hain — approval ek *particular* action ke liye hoti hai, blanket permission
    nahi.

    🔴 Comparison **normalised** values pe hota hai, raw pe nahi. Wajah: row ke
       params JSONB se aate hain, aur JSON round-trip spelling badal sakta hai —
       `{"value": 200}` vs `{"value": "200"}` ka *matlab* ek hai. Raw compare
       karne se ek jhoothi reject aati, aur jhoothi rejects sabse khatarnaak
       cheez karti hain: log unhe bypass karna seekh jaate hain. Isliye row ke
       params bhi layer 3 se guzarte hain, aur phir dono validated dict compare
       hote hain — spelling nahi, matlab.
    """
    if approval.decision != "approved":
        got = approval.decision or "pending"
        raise RejectedError(4, f"approval is {got}, not 'approved'")

    if approval.incident_id != incident_id:
        raise RejectedError(4, f"approval belongs to incident {approval.incident_id}, not {incident_id}")

    if approval.attempt != attempt:
        raise RejectedError(4, f"approval belongs to attempt {approval.attempt}, not {attempt}")

    if approval.action != action:
        raise RejectedError(4, f"approved action was {approval.action!r}, about to run {action!r}")

    # Row ke params ko usi layer 3 se guzaro. Agar row hi invalid hai, toh wo
    # layer 4 ki galti hai (approved row runnable nahi hai), layer 3 ki nahi.
    try:
        approved = _layer3_params(action, approval.params)
    except RejectedError as exc:
        raise RejectedError(4, f"approved row's own params are invalid — {exc.reason}") from None

    if approved != dict(validated):
        raise RejectedError(4, f"params changed: approved {approved}, about to run {dict(validated)}")


# ═════════════════════════════════════════════════════════════════════════════
# LAYER 5 · Attempt budget bacha hai?
# ═════════════════════════════════════════════════════════════════════════════


def _layer5_attempt(attempt: object) -> int:
    """Sawaal: "hum is incident pe kitni baar koshish kar chuke hain?"

    Ye security nahi, **runaway loop** guard hai. Ek agent jo har fail hone pe
    dobara koshish karta hai, wo ek infinite loop hai jo asli containers ko
    restart karta rehta hai. Wo outage nahi theek karta — wo outage *hai*.

    🔴 Off-by-one saaf likhna zaruri hai, kyunki teen jagah teen number dikhte
       hain aur wo teeno consistent hain:

           config.py       : MAX_ATTEMPTS = 3
           init.sql        : CHECK (attempt BETWEEN 0 AND 3)
           ye layer        : 0 <= attempt < 3

       `attempt` ka matlab hai "**ab tak kitni koshish ho chuki**". Toh:
           attempt=0 → pehli koshish chalti hai
           attempt=1 → doosri
           attempt=2 → teesri  (yahan MAX_ATTEMPTS khatam)
           attempt=3 → koi koshish nahi, incident ESCALATED
       Yaani teen executions hoti hain (0, 1, 2), aur DB `3` store kar sakta hai
       kyunki wo *khatam hone ke baad* ki recorded value hai. `<=` likhna char
       koshish kar deta — config.py ki line "4th attempt nahi hoti" isi ko
       rokti hai.
    """
    if isinstance(attempt, bool) or not isinstance(attempt, int):
        raise RejectedError(5, f"attempt must be an int, got {type(attempt).__name__}")
    if attempt < 0:
        raise RejectedError(5, f"attempt {attempt} is negative")
    if attempt >= MAX_ATTEMPTS:
        raise RejectedError(
            5,
            f"attempt budget exhausted (max {MAX_ATTEMPTS}) — escalate to a human",
        )
    return attempt


# ═════════════════════════════════════════════════════════════════════════════
# Public API · validate_shape() → layers 1-3 · validate() → saari 5
# ═════════════════════════════════════════════════════════════════════════════


def validate_shape(action: object, params: Mapping[str, Any]) -> dict[str, Any]:
    """Layers 1–3 sirf. **Ye execute karne ke liye kaafi NAHI hai.**

    Kiske liye hai: UI/worker jab LLM ka proposal DB mein likhne se pehle
    sanity-check karna chahte hain. Ek invalid proposal ko approval queue mein
    daalna insaan ka waqt barbaad karna hai — wo screen padhega, approve
    karega, aur phir broker use reject karega.

    🔴 Naam `validate_shape` hai, `validate_partial` ya `validate_basic` nahi.
       Naam mein "shape" hai taaki call site padhne wale ko saaf dikhe ki
       approval aur budget check *nahi* hue. Ek function jiska naam
       `validate()` ho aur jo aadha validate kare, wo ek din execute path mein
       ghus jaayega — kyunki naam ne jhooth bola tha.
    """
    name = _layer1_name(action)
    _layer2_service(name, params)
    return _layer3_params(name, params)


def validate(
    action: object,
    params: Mapping[str, Any],
    *,
    approval: ApprovalRow,
    incident_id: int,
    attempt: object,
) -> dict[str, Any]:
    """Saari 5 layers. Execute path sirf isse hokar jaata hai.

    🔴 `approval`, `incident_id`, `attempt` keyword-only hain aur inka koi
       **default nahi** hai. Ye jaan-boojh kar hai: agar `approval=None` ek
       legal default hota, toh `validate(action, params)` chal jaata aur
       chupchaap layer 4–5 skip kar deta. Ek security check jo default se off
       ho, wo off hi rehta hai. Yahan usse skip karne ka koi *tareeka* nahi —
       Python khud `TypeError` deta hai.

    🔴 Layer 5 layer 4 se PEHLE chalti hai, aur ye ek soch-samajh ke liya
       faisla hai (numbers layers ko *naam* dete hain, order nahi):
       budget khatam hona ek **terminal** haalat hai — uska jawab "proposal
       theek karo" nahi, "koshish band karo, insaan ko bulao" hai. Agar dono
       galat hon (budget bhi khatam, params bhi badle), toh operator ko pehle
       terminal baat pata chalni chahiye. Ulta order use params debug karne
       bhej deta, jabki asli baat thi ki incident retries khatam kar chuka hai.
    """
    validated = validate_shape(action, params)
    name = str(action)
    _layer5_attempt(attempt)
    _layer4_approval(name, validated, approval, incident_id, int(attempt))  # type: ignore[arg-type]
    return validated


# ═════════════════════════════════════════════════════════════════════════════
# execute() — validate, log, phir dispatch. Isi order mein.
# ═════════════════════════════════════════════════════════════════════════════

#: Audit log callable ka shape: `log(kind, payload)`. Ye `events` table ke
#: `(kind, payload jsonb)` columns se match karta hai (init.sql:96) — taaki
#: caller seedha row insert kar sake, koi translation layer na chahiye.
LogFn = Callable[[str, dict[str, Any]], None]


def execute(
    action: object,
    params: Mapping[str, Any],
    *,
    approval: ApprovalRow,
    incident_id: int,
    attempt: object,
    handlers: Mapping[str, Callable[..., "ActionResult"]] | None = None,
    log: LogFn | None = None,
) -> "ActionResult":
    """Validate → log → dispatch. Poora action path yahi hai.

    🔥 **Log pehle, execute baad mein.** Ye ek line ka faisla hai jiska poora
    matlab crash ke waqt dikhta hai. Agar hum baad mein log karein:

        handler chalao  →  💥 process mar gaya  →  log kabhi nahi hua

    Toh trace mein us action ka koi zikr nahi hoga — aur wo *theek wahi* action
    hai jise dekhna sabse zaruri tha, kyunki usne process gira diya. Log-after
    exactly un events ko kho deta hai jo sabse zyada matter karte hain
    (survivorship bias, code mein).

    Log-before ki keemat: ek action jo log hua par chala nahi (validate ke baad
    crash). Wo bhi galat hai, lekin **kam khatarnaak** — "maybe chala tha"
    ka jawab dhoondha ja sakta hai (container inspect karo). "Kuch hua hi nahi
    dikh raha" ka jawab dhoondhne ka koi tareeka nahi hota.

    🔴 Rejection bhi log hoti hai. Agar sirf successful actions log hotin, toh
       ek attack ke dauraan trace **saaf** dikhta — 20 rejected proposals
       invisible, aur log dekh kar lagta ki sab normal hai. Reject hona ek
       event hai, non-event nahi.

    🔴 Handler ka exception yahan catch NAHI hota. Ek Docker failure worker tak
       jaani chahiye, jo use `events` mein likhta hai aur incident ko aage
       badhata hai (retry ya escalate). Yahan `try/except` lagana ek asli
       failure ko `ok=False` mein badal deta — aur wo do bilkul alag cheezein
       hain: "command chali aur fail hui" vs "command chal hi nahi payi".
    """
    try:
        validated = validate(
            action, params, approval=approval, incident_id=incident_id, attempt=attempt
        )
    except RejectedError as exc:
        if log is not None:
            log(
                "execute",
                {
                    "phase": "rejected",
                    "layer": exc.layer,
                    "reason": exc.reason,
                    "action": action if isinstance(action, str) else repr(action),
                    "incident_id": incident_id,
                    "attempt": attempt,
                },
            )
        raise

    name = str(action)

    if log is not None:
        log(
            "execute",
            {
                "phase": "about_to_run",
                "action": name,
                "params": validated,
                "incident_id": incident_id,
                "attempt": attempt,
            },
        )

    # 🔴 Import yahan, module top pe nahi — poori wajah file ke shuru mein likhi
    #    hai (validate() ko Docker SDK ki zarurat nahi honi chahiye).
    if handlers is None:
        from oncall.actions.handlers import HANDLERS as _real

        handlers = _real

    # 🔴 `handlers[name]` ka KeyError ek programming error hai, LLM ki galti
    #    nahi — layer 1 already garantee kar chuki hai ki naam allowlist mein
    #    hai, aur `handlers.py:399` garantee karta hai ki allowlist ke har naam
    #    ka handler hai. Toh yahan miss hona matlab kisi ne `handlers=` mein
    #    adhoora table bheja (test se). `RejectedError` galat hota — ye reject
    #    nahi, bug hai.
    try:
        handler = handlers[name]
    except KeyError:
        raise RuntimeError(
            f"handler table mein {name!r} nahi hai. "
            "Ye broker ka reject nahi — caller ne adhoora handlers= bheja hai."
        ) from None

    return handler(**validated)



