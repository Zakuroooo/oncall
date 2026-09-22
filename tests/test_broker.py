"""test_broker.py — security boundary ke adversarial tests.

🔒 `CLAUDE.md` §3 ki chaar jagahon mein **chauthi**:

    allowlist.py   → naam + params + kya allowed hai   (authority)
    handlers.py    → har naam ke liye asli Docker call
    llm/schema.py  → Literal[...] taaki LLM galat naam bhej hi na sake
    test_broker.py → ye file: kaunsi layer ne reject kiya

"Adding an action means editing four places… Three out of four is a hole, not a
partial job." Ye file wo chauthi jagah hai — aur is file ka kaam teen hissa hai:

    Hissa A (§0–§5) — har layer pe hamla karo aur poochho: reject hua ya nahi,
                      aur **kis layer ne** kiya.
    Hissa B (§6)    — `execute()` ka contract: log pehle, dispatch baad mein;
                      reject hone par bhi log; aur security args ka koi default
                      nahi.
    Hissa C (§7–§8) — chaaron jagahon ko aur taxonomy ko **aaj** compare karo,
                      AST se — import se nahi.

────────────────────────────────────────────────────────────────────────────
🔴 Kyun `exc.layer == 3`, aur kyun message match nahi
────────────────────────────────────────────────────────────────────────────

Ek test do tarah se likha ja sakta hai:

    assert "allowed nahi" in str(exc)   # ❌ prose pin kar raha hai
    assert exc.layer == 3               # ✅ behaviour pin kar raha hai

Pehla wala ek din typo theek karne pe fail karega, jabki security behaviour
bilkul nahi badla. Us din insaan test ko "theek" karega — aur test ki authority
khatam. Isliye `RejectedError.layer` ek **int** hai, message nahi.

Aur `layer` number khud ek check hai: socho layer 3 ka path check galti se
delete ho jaaye. `/var/lib/postgresql/data` phir bhi reject hoga — lekin layer 1
se (naam theek hai, par… nahi, ruko: naam theek hai toh L1 pass karega). Asli
misaal: layer 2 ka service check hat jaaye aur `oncall-db` L3 ke type check se
reject ho jaaye. Sirf "reject hua" dekhne wala test **green** rehta hai aur hole
zinda rehta hai. Layer number reject hone ki *wajah* pin karta hai.

────────────────────────────────────────────────────────────────────────────
🔴 Ye tests bina Docker, bina pydantic, bina pytest chalte hain
────────────────────────────────────────────────────────────────────────────

    python3 tests/test_broker.py       # aaj, kisi bhi machine pe
    python3 -m pytest tests/ -q       # `make install` ke baad

`broker.py` ki layers 1–5 sirf string aur dict compare karti hain — koi Docker
call nahi. Isliye ye file `import docker` nahi chhoti. Hissa C bhi AST se padhta
hai, import se nahi (`handlers.py` `import docker` karta hai, `schema.py`
pydantic — dono is environment mein install nahi hain). Ek check jo apni
heaviest dependency ke peeche khada ho, wo kam chalta hai; jo kam chalta hai,
wo sad jaata hai.
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent

# 🔴 Ye teen line kyun: `python3 tests/test_broker.py` chalane par Python
#    `sys.path[0]` ko `tests/` set karta hai, repo root ko nahi — toh
#    `import oncall` fail ho jaata hai. `python3 -m pytest` mein CWD add hota
#    hai, isliye wahan kaam kar jaata. Ek test jo sirf ek launcher ke saath
#    chale, wo aadha test hai. Isliye path khud jodte hain.
#
#    Rejected alternative: `tests/__init__.py` + `python3 -m tests.test_broker`.
#    Wo kaam karta, par phir chalane ka tareeka lamba aur yaad rakhne layak
#    nahi rehta — aur jo command yaad na rahe, wo chalayi nahi jaati.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from oncall.actions.allowlist import (  # noqa: E402 - sys.path ke baad hona zaruri hai
    ACTION_NAMES,
    ACTIONS,
    ALLOWED_DB_KEYS,
    ALLOWED_PATHS,
    ALLOWED_SERVICES,
    ALLOWED_TABLES,
)
from oncall.actions.broker import (  # noqa: E402
    ApprovalRow,
    RejectedError,
    execute,
    validate,
    validate_shape,
)
from oncall.config import MAX_ATTEMPTS  # noqa: E402

#: Ek default incident. Number ka koi matlab nahi — bas har jagah wahi rahe,
#: taaki layer 4 ka incident-mismatch test ke liye ek *doosra* number bache.
INCIDENT = 7


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — teen chhote functions, taaki har test ek line ka ho jaaye
# ─────────────────────────────────────────────────────────────────────────────


def _approved(
    action: str = "restart_service",
    params: Mapping[str, Any] | None = None,
    *,
    incident_id: int = INCIDENT,
    attempt: int = 0,
    decision: str | None = "approved",
) -> ApprovalRow:
    """Ek approval row banao. Default = `restart_service(shopapp)`, approved.

    Ye helper jaan-boojh kar **default `decision="approved"`** rakhta hai. Ye
    `broker.validate()` ke ulta hai (wahan koi default nahi hai, D-broker §5).
    Farq wajah se hai: production code mein default-on security check galat hai;
    test helper mein "sab theek hai" wali baseline chahiye, taaki har test sirf
    **ek** cheez galat kare aur hum jaan sakein ki usi ne reject karwaya.
    """
    return ApprovalRow(
        incident_id=incident_id,
        attempt=attempt,
        action=action,
        params={"service": "shopapp"} if params is None else params,
        decision=decision,
    )


def _reject(
    layer: int,
    action: object,
    params: Mapping[str, Any],
    *,
    approval: ApprovalRow | None = None,
    incident_id: int = INCIDENT,
    attempt: object = 0,
) -> RejectedError:
    """`validate()` chalao, expect karo ki reject hoga — aur **usi layer** se.

    Do assert hain, aur dono zaruri hain:
      1. reject hua (warna hole hai)
      2. usi layer se hua (warna hole doosri jagah shift ho gaya hai)
    """
    row = _approved(str(action), dict(params)) if approval is None else approval
    try:
        validate(action, params, approval=row, incident_id=incident_id, attempt=attempt)
    except RejectedError as exc:
        assert exc.layer == layer, (
            f"{action!r} {dict(params)!r} → reject L{exc.layer} se hua, L{layer} expected tha. "
            f"reason: {exc.reason}"
        )
        return exc
    raise AssertionError(f"{action!r} {dict(params)!r} PASS ho gaya — L{layer} ko rokna chahiye tha")


# ═════════════════════════════════════════════════════════════════════════════
# §0 · Happy path — pehle ye saabit karo ki broker kaam bhi karta hai
# ═════════════════════════════════════════════════════════════════════════════
#
# 🔴 Ye test sabse pehle aata hai, aur ye sabse important test hai. Wajah:
#    ek broker jo **sab** reject kar de, wo saare adversarial tests pass kar
#    jaayega. 20 green reject-tests aur ek toota hua happy path = ek security
#    boundary jo poore system ko band kar chuki hai, aur tests khush hain.
#    (Wahi "negative control" wala idea — §7 mein dobara aata hai.)


def test_happy_path_runs() -> None:
    """Sahi action + matching approval → handler chalta hai, validated kwargs ke saath."""
    seen: list[dict[str, Any]] = []
    out = execute(
        "restart_service",
        {"service": "shopapp"},
        approval=_approved(),
        incident_id=INCIDENT,
        attempt=0,
        handlers={"restart_service": lambda **kw: seen.append(kw) or "ok"},
    )
    assert out == "ok", f"handler ka return value gaya kahan? mila {out!r}"
    assert seen == [{"service": "shopapp"}], f"handler ko galat kwargs mile: {seen}"


def test_every_action_has_a_reachable_happy_path() -> None:
    """🔴 Har nau action ke liye ek legal call maujood honi chahiye.

    Ye test un bugs ko pakadta hai jinme broker ek action ko **kabhi** pass
    nahi karta — jaise layer 3 mein ek param ka check likhna bhool jaana
    (`broker.py` ka apna "koi check nahi hai" wala raise). Ek action jo
    allowlist mein hai par broker se nikal hi nahi sakta, wo jhooth hai.
    """
    legal: Mapping[str, dict[str, Any]] = {
        "restart_service": {"service": "shopapp"},
        "start_service": {"service": "shopcache"},
        "stop_service": {"service": "shopapp"},
        "clear_directory": {"service": "shopapp", "path": "/tmp/shopapp-cache"},
        "kill_blocking_queries": {"service": "shopdb"},
        "set_db_setting": {"service": "shopdb", "key": "max_connections", "value": 200},
        "flush_cache": {"service": "shopcache"},
        "vacuum_table": {"service": "shopdb", "table": "orders"},
        "escalate": {"reason": "F4 do baar fail hua, insaan chahiye"},
    }
    assert set(legal) == set(ACTION_NAMES), (
        "is test ki table allowlist se drift ho gayi: "
        f"extra={sorted(set(legal) - set(ACTION_NAMES))} "
        f"gayab={sorted(set(ACTION_NAMES) - set(legal))}"
    )
    for action, params in legal.items():
        out = validate(
            action, params, approval=_approved(action, params), incident_id=INCIDENT, attempt=0
        )
        assert set(out) == set(ACTIONS[action].params), f"{action}: kwargs {sorted(out)} galat hain"


# ═════════════════════════════════════════════════════════════════════════════
# §1 · Layer 1 — naam allowlist mein hai ya nahi
# ═════════════════════════════════════════════════════════════════════════════


def test_layer1_unknown_name() -> None:
    """LLM ne wo action bheja jo maujood hi nahi. Sabse aam hallucination."""
    _reject(1, "rm_rf_slash", {"service": "shopapp"})
    _reject(1, "restart_database", {"service": "shopdb"})
    _reject(1, "", {})


def test_layer1_case_and_whitespace_are_not_forgiven() -> None:
    """🔴 `Restart_Service` aur ` restart_service ` bhi reject hote hain.

    Broker `.lower()` ya `.strip()` **nahi** karta, aur ye jaan-boojh kar hai.
    Normalisation ka matlab hota "main guess kar raha hoon ki iska matlab kya
    tha" — aur security boundary pe guess nahi karna chahiye. Agar LLM sahi
    naam nahi bhej pa raha, wo prompt ki bug hai, broker ki nahi.
    """
    _reject(1, "Restart_Service", {"service": "shopapp"})
    _reject(1, " restart_service", {"service": "shopapp"})


def test_layer1_non_string_action() -> None:
    """`action` string hi hona chahiye — `None`, int, list, dict sab reject.

    🔴 Ye "kabhi nahi hoga" wala case nahi hai: action JSONB row se aata hai,
    aur `{"action": null}` ek perfectly valid JSON hai.
    """
    for bad in (None, 123, 1.5, True, ["restart_service"], {"restart_service": 1}, b"restart"):
        _reject(1, bad, {"service": "shopapp"})


# ═════════════════════════════════════════════════════════════════════════════
# §2 · Layer 2 — blast radius. Kaunsi service touch ho sakti hai.
# ═════════════════════════════════════════════════════════════════════════════


def test_layer2_agent_cannot_touch_its_own_brain() -> None:
    """🔥 Sabse important layer-2 test: `oncall-db` kabhi target nahi ban sakta.

    `oncall-db` mein incidents, actions aur approvals rehte hain — yaani wo
    trace jo saabit karta hai ki agent ne kya kiya. Agar agent use restart ya
    stop kar sakta, toh ek galat proposal apne hi saboot mita deta. Ek system
    jo apni memory uda sakta hai, audit nahi ho sakta.
    """
    assert "oncall-db" not in ALLOWED_SERVICES, "allowlist mein oncall-db ghus gaya hai!"
    for action in ("restart_service", "stop_service", "start_service"):
        _reject(2, action, {"service": "oncall-db"})
    _reject(2, "clear_directory", {"service": "oncall-db", "path": "/var/log/shopapp"})


def test_layer2_per_action_service_scope() -> None:
    """Har action apni hi services pe legal hai — sirf global list se kaam nahi chalta.

    `kill_blocking_queries` sirf `shopdb` pe (Postgres-only baat hai), aur
    `flush_cache` sirf `shopcache` pe (`redis-cli` shopapp mein exist hi nahi
    karta). Ek allowed service + ek allowed action = **phir bhi illegal** ho
    sakta hai. Isliye check `spec.services` pe hai, `ALLOWED_SERVICES` pe nahi.
    """
    _reject(2, "kill_blocking_queries", {"service": "shopapp"})
    _reject(2, "flush_cache", {"service": "shopdb"})
    _reject(2, "vacuum_table", {"service": "shopcache", "table": "orders"})


def test_layer2_escalate_must_not_carry_a_service() -> None:
    """🔴 `escalate` ka `services` khaali frozenset hai — toh `service` bhejna hi galat hai.

    Ye "extra param" wali baat nahi hai (wo layer 3 hai). Ye ek intent check
    hai: agar koi `escalate(reason=..., service="shopdb")` bhej raha hai, toh
    ya wo confused hai ya kuch smuggle kar raha hai. Dono soorat mein ye action
    ek din kisi handler ko service pass kar sakta hai — abhi reject kar do.
    """
    assert ACTIONS["escalate"].services == frozenset(), "escalate ko service mil gayi?"
    _reject(2, "escalate", {"reason": "insaan chahiye", "service": "shopdb"})


def test_layer2_missing_or_bad_type_service() -> None:
    """Service missing ho, ya string na ho — dono L2 pe marte hain."""
    _reject(2, "restart_service", {})
    _reject(2, "restart_service", {"service": None})
    _reject(2, "restart_service", {"service": ["shopapp"]})


# ═════════════════════════════════════════════════════════════════════════════
# §3 · Layer 3 — params ka exact set, aur har value ka apna allowlist
# ═════════════════════════════════════════════════════════════════════════════


def test_layer3_exact_key_set() -> None:
    """🔴 Extra key bhi reject, missing key bhi. Subset nahi — **exact** match.

    Extra key zyada khatarnaak hai, aur wajah `**kwargs` hai. Aaj
    `restart_service(**{"service": "x", "force": True})` `TypeError` deta hai,
    toh lagta hai koi khatra nahi. Lekin jis din kisi handler mein ek naya
    optional param aaya (`force=False`), us din wo extra key ek **asli,
    un-allowlisted argument** ban jaayegi — aur us din koi test nahi tootega.
    Isliye extra key ek explicit reject hai, `TypeError` pe bharosa nahi.
    """
    _reject(3, "restart_service", {"service": "shopapp", "force": True})
    _reject(3, "restart_service", {"service": "shopapp", "timeout": 0})
    _reject(3, "clear_directory", {"service": "shopapp"})  # path missing
    _reject(3, "set_db_setting", {"service": "shopdb", "key": "work_mem"})  # value missing
    _reject(3, "escalate", {})  # reason missing


def test_layer3_path_is_exact_membership_not_prefix() -> None:
    """🔥 Yahi wo test hai jo `startswith()` wali galti ko rokta hai.

    Socho path check aisa hota:

        if not any(path.startswith(p) for p in ALLOWED_PATHS): reject

    Toh `/var/log/shopapp/../../lib/postgresql/data` **pass** kar jaata — wo
    ek allowed prefix se shuru hota hai. Uske baad `find -delete` Postgres ka
    data directory khaali kar deta. Prefix matching ka matlab hai ki aapko `..`
    ka poora problem solve karna padega (normalise → symlink resolve →
    compare), aur us chain mein ek galti = data loss.

    Exact set membership mein `..` ka sawaal hi paida nahi hota. Do allowed
    string hain; baaki poori duniya reject.
    """
    for bad in (
        "/var/lib/postgresql/data",  # THREAT_MODEL T1 — asli target
        "/var/log/shopapp/../../lib/postgresql/data",  # prefix-match ko todta hai
        "/tmp/shopapp-cache/../../etc",
        "/tmp/shopapp-cache/",  # 🔴 trailing slash bhi exact nahi hai
        "/tmp/shopapp-cache/sub",
        "/",
        "",
    ):
        _reject(3, "clear_directory", {"service": "shopapp", "path": bad})
    assert ALLOWED_PATHS == frozenset({"/tmp/shopapp-cache", "/var/log/shopapp"}), (
        "ALLOWED_PATHS badal gaya — is test ki list bhi update karo"
    )


def test_layer3_db_key_allowlist() -> None:
    """🔴 `fsync` reject hota hai, aur ye ek asli attack hai.

    `ALTER SYSTEM SET fsync = off` Postgres ko tez kar deta hai — aur crash pe
    data corrupt. Ek LLM jo "database slow hai" padh kar performance tweak
    dhoondh raha ho, wo ise propose kar sakta hai. Boolean keys allowlist se
    bahar hain, isliye naam hi mar jaata hai — value tak baat nahi pahunchti.
    """
    for bad_key in ("fsync", "full_page_writes", "shared_buffers", "max_conn", "MAX_CONNECTIONS"):
        _reject(3, "set_db_setting", {"service": "shopdb", "key": bad_key, "value": 100})
    assert set(ALLOWED_DB_KEYS) == {"max_connections", "statement_timeout", "work_mem"}, (
        "ALLOWED_DB_KEYS badal gaya — is test ki list bhi update karo"
    )


def test_layer3_value_range() -> None:
    """Range ke bahar value reject. Floor 10 khaas hai — neeche padho."""
    # 🔴 `max_connections = 2` PG15 ko boot hi nahi karne deta:
    #    `superuser_reserved_connections` default 3 hai, aur rule hai "must be
    #    less than max_connections". Ek action jo service ko boot-loop mein daal
    #    de, wo fix nahi — naya fault hai. Isliye floor 10.
    _reject(3, "set_db_setting", {"service": "shopdb", "key": "max_connections", "value": 2})
    _reject(3, "set_db_setting", {"service": "shopdb", "key": "max_connections", "value": 0})
    _reject(3, "set_db_setting", {"service": "shopdb", "key": "max_connections", "value": -1})
    _reject(3, "set_db_setting", {"service": "shopdb", "key": "max_connections", "value": 501})
    _reject(3, "set_db_setting", {"service": "shopdb", "key": "work_mem", "value": 63})
    _reject(3, "set_db_setting", {"service": "shopdb", "key": "statement_timeout", "value": -1})


def test_layer3_the_bool_trap() -> None:
    """🔥 `{"value": true}` reject hota hai — aur ye is file ka sabse pyaara test hai.

    Python mein `bool` **`int` ki subclass hai**:

        isinstance(True, int)  →  True
        int(True)              →  1

    Toh ek naive check — `if isinstance(value, int)` — `True` ko happily accept
    kar leta. Phir `max_connections = 1` chala jaata, jo floor 10 se neeche hai,
    jo Postgres ko boot karne se rok deta. Ek JSON `true` se database mar jaana,
    bas ek missing check ki doori pe.

    Isliye `_as_int()` mein bool ka check **int ke check se pehle** aata hai.
    Order matter karta hai; agar baad mein hota, wo kabhi chalta hi nahi.
    """
    _reject(3, "set_db_setting", {"service": "shopdb", "key": "max_connections", "value": True})
    _reject(3, "set_db_setting", {"service": "shopdb", "key": "max_connections", "value": False})
    # aur uska ulta: attempt bhi bool nahi ho sakta (layer 5 mein wahi guard)
    _reject(5, "restart_service", {"service": "shopapp"}, attempt=True)


def test_layer3_no_silent_casts() -> None:
    """🔴 `str()` ya `float→int` cast nahi hota. Bad input bad rehta hai.

    `str(["a"])` → `"['a']"`. Ek cast bad input ko *kisi doosre* input mein
    badal deta hai, aur wo doosra input allowlist pass kar sakta hai. Reject
    karne se bad input bad rehta hai — aur trace mein dikhta hai.

    ⚠️ Do jaan-boojh kar chhode gaye raste: `value="200"` (string) **allowed**
    hai, kyunki JSONB round-trip number ko string bana sakta hai aur wo
    *meaning* nahi badalta — aur usi wajah se `" 200 "` bhi allowed hai
    (`int(raw.strip())`). `"2.5"` phir bhi reject hai: wo integer nahi hai.
    """
    bad_values = ("2.5", 2.5, "0x10", "1e3", "", "abc", None, [200], {"v": 200})
    for bad in bad_values:
        _reject(3, "set_db_setting", {"service": "shopdb", "key": "max_connections", "value": bad})
    # ✅ aur ye pass hona chahiye — warna JSONB round-trip false-reject deta
    ok = validate_shape("set_db_setting", {"service": "shopdb", "key": "work_mem", "value": "4096"})
    assert ok["value"] == 4096 and isinstance(ok["value"], int), f"string→int normalise toota: {ok}"


def test_layer3_table_allowlist() -> None:
    """`vacuum_table` sirf allowlisted table pe. `pg_class` reject."""
    for bad in ("pg_class", "orders; DROP TABLE orders", "ORDERS", "public.orders", ""):
        _reject(3, "vacuum_table", {"service": "shopdb", "table": bad})
    assert ALLOWED_TABLES == frozenset({"orders"}), "ALLOWED_TABLES badal gaya — test update karo"


def test_layer3_reason_must_say_something() -> None:
    """Khaali ya sirf whitespace `reason` reject; bahut lamba bhi reject.

    Length cap ka maqsad security hai, tameez nahi: `reason` seedha `events`
    table ke JSONB payload mein jaata hai. Ek 2 MB ka reason ek row ko phula
    deta hai aur trace padhne layak nahi rehta.
    """
    _reject(3, "escalate", {"reason": ""})
    _reject(3, "escalate", {"reason": "   \n\t "})
    _reject(3, "escalate", {"reason": "x" * 1001})
    ok = validate_shape("escalate", {"reason": "  F4 do baar fail hua  "})
    assert ok["reason"] == "F4 do baar fail hua", f"strip nahi hua: {ok['reason']!r}"


# ═════════════════════════════════════════════════════════════════════════════
# §4 · Layer 4 — approval ka matlab: *is* action ki permission
# ═════════════════════════════════════════════════════════════════════════════
#
# 🔥 Ye ek hi layer hai jo LLM se nahi, **hum se** bachati hai. Layers 1–3 ka
#    dushman ek hallucinating model hai. Layer 4 ka dushman ek race hai:
#
#      insaan `restart_service(shopapp)` approve karta hai
#           ⏳ ... minute? ghante? ...
#      row edit ho jaati hai → action = "stop_service"
#      worker `decision == 'approved'` dekhta hai ✅ aur chala deta hai
#
#    Insaan ne approve **kiya** tha — lekin jo chala, us pe nahi. Gate maujood
#    tha aur bekaar tha. Isliye approval ek *particular* action ki hoti hai,
#    blanket permission nahi. Ye TOCTOU hai: check sahi tha, use galat ho gaya.


def test_layer4_only_approved_runs() -> None:
    """`pending` (None) aur `rejected` — dono chalne nahi chahiye."""
    for decision in (None, "rejected", "APPROVED", "approve", "", "auto"):
        _reject(
            4,
            "restart_service",
            {"service": "shopapp"},
            approval=_approved(decision=decision),
        )


def test_layer4_approval_belongs_to_this_incident_and_attempt() -> None:
    """Doosre incident ki (ya doosre attempt ki) approval reuse nahi ho sakti.

    Bina is check ke ek purana approved row ek naye incident ka passport ban
    jaata — "pichhli baar restart approve hua tha, toh ab bhi chalega". Approval
    ek (incident, attempt) jodi ki hoti hai, ek naam ki nahi.
    """
    _reject(4, "restart_service", {"service": "shopapp"}, approval=_approved(incident_id=99))
    _reject(4, "restart_service", {"service": "shopapp"}, approval=_approved(attempt=1))


def test_layer4_action_swapped_after_approval() -> None:
    """🔥 Asli hamla: approve `restart_service` hua, chalne wala hai `stop_service`."""
    exc = _reject(
        4,
        "stop_service",
        {"service": "shopapp"},
        approval=_approved("restart_service", {"service": "shopapp"}),
    )
    assert "restart_service" in exc.reason and "stop_service" in exc.reason, (
        f"reason mein dono naam hone chahiye (operator ko farq dikhna chahiye): {exc.reason}"
    )


def test_layer4_params_swapped_after_approval() -> None:
    """Wahi action, doosra target. Naam match karta hai — phir bhi reject.

    `restart_service(shopapp)` approve hua aur `restart_service(shopdb)` chalne
    wala hai. Sirf action naam compare karne wala check ise pass kar deta, aur
    database restart ho jaata — jise kisi ne approve nahi kiya tha.
    """
    _reject(
        4,
        "restart_service",
        {"service": "shopdb"},
        approval=_approved("restart_service", {"service": "shopapp"}),
    )
    _reject(
        4,
        "clear_directory",
        {"service": "shopapp", "path": "/var/log/shopapp"},
        approval=_approved("clear_directory", {"service": "shopapp", "path": "/tmp/shopapp-cache"}),
    )


def test_layer4_compares_meaning_not_spelling() -> None:
    """🔴 JSONB round-trip `200` ko `"200"` bana de — ye **false reject** nahi hona chahiye.

    Postgres JSONB se wapas aane par ek number string ban sakta hai. Raw
    comparison (`row.params != validated`) us soorat mein reject karta, aur wo
    reject *galat* hota. Aur false rejects khatarnaak hote hain — kisi bhi
    security check ka sabse aam maut ka kaaran ye hai ki logon ne use bypass
    karna seekh liya, kyunki wo "bekaar mein" mana karta tha.

    Isliye row ke params bhi layer 3 se guzarte hain, aur **do validated dict**
    compare hote hain. Matlab compare hota hai, spelling nahi.
    """
    row = _approved(
        "set_db_setting",
        {"service": "shopdb", "key": "max_connections", "value": "200"},  # string!
    )
    out = validate(
        "set_db_setting",
        {"service": "shopdb", "key": "max_connections", "value": 200},  # int!
        approval=row,
        incident_id=INCIDENT,
        attempt=0,
    )
    assert out == {"service": "shopdb", "key": "max_connections", "value": 200}, out


def test_layer4_unrunnable_approved_row_is_layer4_not_layer3() -> None:
    """🔴 Agar approved row ke params khud invalid hain, wo **L4** hai, L3 nahi.

    Attribution matter karti hai. L3 ka matlab hai "LLM ne galat proposal
    bheja". Yahan LLM ka proposal theek hai — **approved row** hi chalne layak
    nahi hai. Do alag bugs, do alag jagah dekhne ko: L3 → prompt/schema,
    L4 → approval likhne wala code ya row tampering.
    """
    exc = _reject(
        4,
        "clear_directory",
        {"service": "shopapp", "path": "/tmp/shopapp-cache"},
        approval=_approved("clear_directory", {"service": "shopapp", "path": "/etc"}),
    )
    assert "approved row" in exc.reason, f"attribution message badal gaya: {exc.reason}"
    _reject(
        4,
        "restart_service",
        {"service": "shopapp"},
        approval=_approved("restart_service", {"service": "shopapp", "force": True}),
    )


# ═════════════════════════════════════════════════════════════════════════════
# §5 · Layer 5 — retry budget. Runaway loop ka guard.
# ═════════════════════════════════════════════════════════════════════════════


def test_layer5_budget_is_three_attempts() -> None:
    """`attempt` = "kitni koshishein **ho chuki** hain". Toh 0, 1, 2 chalte hain; 3 nahi.

    Teen file ek hi number pe sahmat hain, aur teeno padhna zaruri hai:

        config.py   MAX_ATTEMPTS = 3
        init.sql    CHECK (attempt BETWEEN 0 AND 3)
        broker.py   0 <= attempt < 3

    DB 3 store kar sakta hai kyunki 3 wo *recorded* value hai jab budget khatam
    ho chuka (incident ESCALATED). Lekin `attempt=3` pe **kuch nahi chalta**.
    `<=` likhna chautha attempt allow kar deta — jo `config.py` ke "4th attempt
    nahi hoti" ke khilaaf hai.
    """
    assert MAX_ATTEMPTS == 3, f"MAX_ATTEMPTS {MAX_ATTEMPTS} ho gaya — ye test update karo"
    for ok_attempt in (0, 1, 2):
        validate(
            "restart_service",
            {"service": "shopapp"},
            approval=_approved(attempt=ok_attempt),
            incident_id=INCIDENT,
            attempt=ok_attempt,
        )
    for bad in (3, 4, 99, -1, -100):
        _reject(5, "restart_service", {"service": "shopapp"}, approval=_approved(attempt=bad), attempt=bad)


def test_layer5_runs_before_layer4() -> None:
    """🔴 Dono galat hon toh operator ko **L5** dikhna chahiye, L4 nahi.

    Layer ke numbers layers ko *naam* dete hain, order dictate nahi karte.
    Budget khatam hona terminal hai: jawab "koshish band karo, insaan bulao"
    hai, "proposal theek karo" nahi. Agar L4 pehle chalta, operator ko "params
    match nahi kar rahe" dikhta aur wo params theek karne baithta — jabki asli
    baat ye thi ki 3 koshishein ho chuki hain aur ab rukna hai.
    """
    exc = _reject(
        5,
        "stop_service",  # L4 bhi fail karega (approve `restart_service` hua tha)
        {"service": "shopapp"},
        approval=_approved("restart_service", {"service": "shopapp"}, attempt=0),
        attempt=3,  # L5 bhi fail karega
    )
    assert "budget" in exc.reason, f"L5 ka reason budget ke baare mein hona chahiye: {exc.reason}"


def test_layer5_non_int_attempt() -> None:
    """`attempt` int hi hona chahiye — string "0" bhi reject.

    ⚠️ Yahan `_as_int` ki string-tolerance jaan-boojh kar **nahi** hai. Farq:
    `params` LLM/JSONB se aate hain (wahan string tolerable hai), lekin
    `attempt` hamare **apne** worker se aata hai, ek `integer` column se. Agar
    wahan string aa rahi hai, toh hamare code mein bug hai — usse chupa kar
    cast karna wo bug baad mein, kisi aur jagah, dikhaayega.
    """
    for bad in ("0", "3", None, 1.0, True, False, [0]):
        _reject(5, "restart_service", {"service": "shopapp"}, attempt=bad)


# ═════════════════════════════════════════════════════════════════════════════
# §6 · execute() — logging, dispatch, aur "reject hua toh haath bhi nahi lagaya"
# ═════════════════════════════════════════════════════════════════════════════


def test_log_fires_before_the_handler_runs() -> None:
    """🔴 Log **pehle**, execute **baad mein**. Order hi poora point hai.

    Log-after ka failure mode: jo action process ko maar de, wo hi log mein
    nahi aata. Yaani aapka trace un actions se bhara hai jo survive kar gaye —
    survivorship bias, code mein. Aur crash ke baad aap us ek action ko dhoondh
    rahe honge jiska koi record nahi.

    Log-before ka cost halka hai: "log hai, chala ya nahi pata nahi". Wo sawaal
    container dekh kar answer ho jaata hai. "Kuch hua hi nahi lagta" answer
    nahi hota.
    """
    order: list[str] = []
    execute(
        "restart_service",
        {"service": "shopapp"},
        approval=_approved(),
        incident_id=INCIDENT,
        attempt=0,
        handlers={"restart_service": lambda **kw: order.append("handler")},
        log=lambda kind, payload: order.append(f"log:{payload['phase']}"),
    )
    assert order == ["log:about_to_run", "handler"], f"order galat hai: {order}"


def test_rejection_is_logged_too() -> None:
    """🔴 Reject hona ek **event** hai, non-event nahi.

    Agar sirf successes log hote, toh ek attack ke dauran trace bilkul **saaf**
    dikhta: 20 rejected proposals kahin nahi, aur ek bhi red flag nahi. Jo
    system apni rejections nahi likhta, wo apne upar hue hamle ka koi record
    nahi rakhta.
    """
    logged: list[dict[str, Any]] = []
    try:
        execute(
            "rm_rf_slash",
            {"service": "shopapp"},
            approval=_approved(),
            incident_id=INCIDENT,
            attempt=0,
            handlers={},
            log=lambda kind, payload: logged.append(payload),
        )
    except RejectedError:
        pass
    else:  # pragma: no cover - agar yahan pahunche toh broker toota hai
        raise AssertionError("unknown action execute ho gaya")
    assert len(logged) == 1, f"reject pe {len(logged)} log aaye, 1 chahiye"
    assert logged[0]["phase"] == "rejected", logged[0]
    assert logged[0]["layer"] == 1, logged[0]
    assert logged[0]["incident_id"] == INCIDENT, logged[0]


def test_rejected_action_never_reaches_a_handler() -> None:
    """Har layer ke liye: handler ko **bulaya hi nahi** jaana chahiye.

    Ye "reject hua" se strong claim hai. Reject hone ke baad bhi agar handler
    chal gaya (jaise galti se `try/except` ke andar dispatch), toh reject ka
    koi matlab nahi. Isliye ek fake handler table jo chhoo jaane par shor karta
    hai.
    """
    touched: list[str] = []
    noisy = {name: (lambda _n=name, **kw: touched.append(_n)) for name in ACTION_NAMES}
    attacks: tuple[tuple[str, dict[str, Any], ApprovalRow, object], ...] = (
        ("rm_rf", {"service": "shopapp"}, _approved("rm_rf", {"service": "shopapp"}), 0),
        ("stop_service", {"service": "oncall-db"}, _approved("stop_service", {"service": "oncall-db"}), 0),
        ("restart_service", {"service": "shopapp", "x": 1}, _approved("restart_service", {"service": "shopapp", "x": 1}), 0),
        ("clear_directory", {"service": "shopdb", "path": "/var/lib/postgresql/data"}, _approved("clear_directory", {"service": "shopdb", "path": "/var/lib/postgresql/data"}), 0),
        ("stop_service", {"service": "shopapp"}, _approved("restart_service", {"service": "shopapp"}), 0),
        ("restart_service", {"service": "shopapp"}, _approved(), 3),
    )
    for action, params, row, attempt in attacks:
        try:
            execute(action, params, approval=row, incident_id=INCIDENT, attempt=attempt, handlers=noisy)
        except RejectedError:
            continue
        raise AssertionError(f"{action} {params} execute ho gaya")
    assert touched == [], f"reject hone ke baad bhi ye handlers chale: {touched}"


def test_security_arguments_have_no_default() -> None:
    """🔴 Ek security check jo default se off ho, wo off hi rehta hai.

    Socho `approval: ApprovalRow | None = None` hota. Phir `validate(action,
    params)` **chal jaata** aur chupchaap layer 4 aur 5 skip kar deta. Koi
    exception nahi, koi log nahi — bas ek call site jo aadha validate karta
    hai. Aisa bug code review mein dikhta bhi nahi, kyunki call site bilkul
    normal lagta hai.

    Isliye teeno keyword-only hain aur inka default nahi hai: skip karne ka
    koi *tareeka* hi nahi bachta, Python khud `TypeError` deta hai.

    Do tareeke se check karte hain — dono zaruri hain:
      1. Signature padho (`inspect`) — ye batata hai ki default add hua kya.
      2. Bina arg call karo — ye batata hai ki asal mein `TypeError` aata hai.
    """
    guarded = ("approval", "incident_id", "attempt")
    for fn in (validate, execute):
        sig = inspect.signature(fn)
        for arg in guarded:
            p = sig.parameters[arg]
            assert p.kind is inspect.Parameter.KEYWORD_ONLY, (
                f"{fn.__name__}({arg}) keyword-only nahi hai — positional call "
                f"se order galat ho sakta hai"
            )
            assert p.default is inspect.Parameter.empty, (
                f"{fn.__name__}({arg}) ka default {p.default!r} hai. "
                f"Security arg ka default = check off by default."
            )

    try:
        validate("restart_service", {"service": "shopapp"})  # type: ignore[call-arg]
    except TypeError:
        pass
    else:  # pragma: no cover - signature toot chuki hogi
        raise AssertionError("validate() bina approval/incident_id/attempt chal gaya")

    try:
        execute("restart_service", {"service": "shopapp"}, handlers={})  # type: ignore[call-arg]
    except TypeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("execute() bina approval/incident_id/attempt chal gaya")


def test_execute_only_accepts_str_and_mapping_not_a_proposal_object() -> None:
    """🔴 TOCTOU: broker ko wo object nahi milna chahiye jise wo trust kar le.

    Ye rule hai: broker `ProposedAction` (pydantic model) accept **nahi** karta,
    sirf `str` + `Mapping`. Wajah: agar wo model accept karta, toh ek din koi
    `model.validated = True` jaisa field dekh kar layer 1–3 skip kar deta —
    "ye toh already validate ho chuka hai". Trust ek attribute ban jaata, aur
    attribute badla ja sakta hai.

    Yahan hum us contract ko behaviour se pin karte hain: ek aisa object jo
    `.action` aur `.params` rakhta hai (bilkul `ProposedAction` jaisa) layer 1
    se reject hota hai, kyunki wo `str` nahi hai.
    """

    class FakeProposal:
        action = "restart_service"
        params = {"service": "shopapp"}
        validated = True  # 🔴 jhoota bharosa — broker ise dekhta hi nahi

    exc = _reject(1, FakeProposal(), {"service": "shopapp"})
    assert "string" in exc.reason, exc.reason


# ═════════════════════════════════════════════════════════════════════════════
# §7 · HISSA C — chaar jagah, AST se. Import se nahi.
# ═════════════════════════════════════════════════════════════════════════════
#
# 🔴 Yahan `import` kyun nahi kar sakte, aur AST kyun:
#
#   `handlers.py` sabse pehli line pe `import docker` karta hai. `schema.py` ko
#   pydantic chahiye. Is machine pe dono install nahi hain (network sirf do
#   hosts allow karta hai, `pip install` possible nahi). Agar ye check import
#   pe khada hota, toh wo **aaj chalta hi nahi** — aur CLAUDE.md §3 ka pehla
#   enforcement point ek `pip install` ke peeche chala jaata.
#
#   AST = Abstract Syntax Tree. `ast.parse()` file ko *padhta* hai aur uska
#   structure deta hai, bina ek line chalaye. `import docker` ek `ast.Import`
#   node ban jaata hai — Docker chalu hona zaruri nahi.
#
# 🔴 Aur grep se kyun nahi: `command grep -n '"restart_service"' handlers.py`
#   docstrings, comments aur asli dict — teeno pe match karta hai. Grep ko
#   syntax nahi pata. AST ko pata hai: comment tree mein maujood hi nahi hota.
#   (Yahi lesson `test_no_shell.py` mein bhi likha hai.)


def _parse(rel: str | Path) -> ast.Module:
    """Ek file ko AST mein padho. Chalaya nahi jaata.

    `rel` repo-root ke relative string ho sakta hai, ya ek absolute `Path` —
    absolute wala sirf negative control ke liye hai, jo temp mein ek nakli file
    plant karke check karta hai ki parser asal mein padh raha hai.
    """
    path = Path(rel) if isinstance(rel, Path) else REPO_ROOT / rel
    assert path.exists(), f"{rel} maujood nahi hai — CLAUDE.md §3 ki ek jagah gayab"
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _dict_keys_of(rel: str | Path, var: str) -> tuple[str, ...]:
    """`var = {...}` ya `var: T = {...}` ki string keys, file mein likhe order mein.

    `ast.AnnAssign` = annotated assignment (`HANDLERS: Mapping[...] = {...}`).
    `ast.Assign` = plain (`X = {...}`). Dono handle karte hain kyunki annotation
    add/remove karna ek routine refactor hai, aur us refactor pe ye check toot
    nahi jaana chahiye.
    """
    for node in ast.walk(_parse(rel)):
        target: ast.expr | None = None
        if isinstance(node, ast.AnnAssign):
            target = node.target
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id != var:
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        return tuple(
            k.value
            for k in node.value.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        )
    raise AssertionError(f"{rel} mein `{var} = {{...}}` nahi mila")


def _literal_members_of(rel: str | Path, var: str) -> tuple[str, ...]:
    """`var = Literal["a", "b", ...]` ke andar ki strings.

    `Literal[...]` AST mein ek `ast.Subscript` hai: `value` = `Name('Literal')`,
    `slice` = `ast.Tuple` (ek se zyada member) ya seedha `ast.Constant` (ek hi
    member). Dono shapes handle karte hain — warna ek din 1-action allowlist pe
    ye check chupchaap 0 naam padhta.
    """
    for node in ast.walk(_parse(rel)):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if not isinstance(tgt, ast.Name) or tgt.id != var:
            continue
        sub = node.value
        if not isinstance(sub, ast.Subscript):
            continue
        sl = sub.slice
        parts = sl.elts if isinstance(sl, ast.Tuple) else [sl]
        return tuple(
            p.value for p in parts if isinstance(p, ast.Constant) and isinstance(p.value, str)
        )
    raise AssertionError(f"{rel} mein `{var} = Literal[...]` nahi mila")


def test_four_places_agree() -> None:
    """🔒 CLAUDE.md §3 ka enforcement: chaar jagah, ek hi list.

    Ye test wo failure pakadta hai jo review mein sabse aasani se nikal jaata
    hai: nayi action `allowlist.py` mein add ho gayi, `handlers.py` mein bhool
    gaye. Us case mein action **allowed** hai (layer 1 pass), aur crash dispatch
    ke waqt hota hai — production mein, ek approved incident ke beech.

    Ulta case bhi bura hai: handler maujood hai par allowlist mein nahi. Wo dead
    code hai jise koi allowlist entry ek din "chalu" kar degi, bina uske design
    review ke.
    """
    handler_names = _dict_keys_of("oncall/actions/handlers.py", "HANDLERS")
    literal_names = _literal_members_of("oncall/llm/schema.py", "ActionName")
    allow = set(ACTION_NAMES)

    assert set(handler_names) == allow, (
        "handlers.py drift:\n"
        f"  handler hai par allowlist mein nahi : {sorted(set(handler_names) - allow)}\n"
        f"  allowlist mein hai par handler nahi : {sorted(allow - set(handler_names))}"
    )
    assert set(literal_names) == allow, (
        "schema.py ka Literal drift:\n"
        f"  Literal mein extra : {sorted(set(literal_names) - allow)}\n"
        f"  Literal mein gayab : {sorted(allow - set(literal_names))}"
    )
    # Duplicate naam set-compare se chhup jaate hain — length se pakde jaate hain.
    assert len(handler_names) == len(allow), f"HANDLERS mein duplicate key: {handler_names}"
    assert len(literal_names) == len(allow), f"Literal mein duplicate: {literal_names}"


# ═════════════════════════════════════════════════════════════════════════════
# §8 · Taxonomy coverage — allowlist ka answer key se milaan, BINA use padhe
# ═════════════════════════════════════════════════════════════════════════════
#
# `allowlist.py` khud kehta hai (line ~352) ki ek invariant wo likh nahi sakti:
#
#     "taxonomy ke har fault ka correct_action is allowlist mein hona chahiye"
#
# Kyunki us check ke liye `chaos.faults` import karna padta, aur 🔴 CLAUDE.md §3
# kehta hai: "nothing under oncall/ may import from chaos/ or read
# correct_action". Agar allowlist wo import karti, toh agent ke **apne process**
# mein answer key load ho jaati — aur "kya agent cheat kar raha hai?" ka jawab
# "nahi" se "abhi tak nahi" ban jaata.
#
# Test file `oncall/` ke bahar hai aur runtime pe load nahi hoti, toh wo dono
# taraf dekh sakti hai. Lekin ye file bhi `chaos.faults` **import nahi** karti —
# AST se padhti hai. Wajah: `faults.py` top pe `import docker` karta hai (heal/
# break functions ko chahiye), aur wo is machine pe install nahi hai.
#
# 🔴 Ye check kya pakadta hai: ek fault jiska sahi jawab allowlist mein hi nahi
#    hai. Us haalat mein wo fault **unfixable** hai — agent kabhi pass nahi kar
#    sakta, chahe diagnosis perfect ho. Aur eval report usse "agent fail hua"
#    dikhati, jabki asli baat thi ki menu mein jawab hi nahi tha. Ye galti is
#    repo mein **asal mein hui thi**: docs ki purani 9-item list mein F3 ka
#    `kill_blocking_queries` maujood hi nahi tha.


def _fault_ground_truth(rel: str | Path = "chaos/faults.py") -> list[dict[str, Any]]:
    """`chaos/faults.py` ke `Fault(...)` calls se sirf teen field nikaalo.

    Import nahi — `ast.parse`. Har `Fault(` call ke keywords padhte hain:
    `id`, `correct_action`, `correct_target`, `also_acceptable`.

    `rel` param sirf negative control ke liye hai (plant kiya hua nakli file).
    """
    out: list[dict[str, Any]] = []
    for node in ast.walk(_parse(rel)):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "Fault":
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg is not None}
        row: dict[str, Any] = {"lineno": node.lineno}
        for field in ("id", "correct_action", "correct_target"):
            v = kw.get(field)
            row[field] = v.value if isinstance(v, ast.Constant) else None
        alt = kw.get("also_acceptable")
        row["also_acceptable"] = (
            tuple(
                e.value
                for e in alt.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            )
            if isinstance(alt, ast.Tuple)
            else ()
        )
        out.append(row)
    return out


def test_every_fault_has_a_correct_action_in_the_allowlist() -> None:
    """Har fault ka sahi jawab menu mein maujood hona chahiye — warna wo fault
    unfixable hai aur eval report jhoot bolti hai ("agent fail hua", jabki jawab
    hi available nahi tha)."""
    faults = _fault_ground_truth()
    assert len(faults) == 5, f"{len(faults)} faults mile, taxonomy mein 5 hain"

    for f in faults:
        fid = f["id"]
        assert fid, f"faults.py:{f['lineno']} — `id` padha nahi ja saka"
        assert f["correct_action"] in ACTIONS, (
            f"{fid} ka correct_action {f['correct_action']!r} allowlist mein nahi hai — "
            f"ye fault unfixable hai"
        )
        for alt in f["also_acceptable"]:
            assert alt in ACTIONS, f"{fid} ka also_acceptable {alt!r} allowlist mein nahi hai"

        # 🔴 Sirf "maujood hai" kaafi nahi — wo action distractor nahi hona
        #    chahiye. Warna oracle ek distractor ko pass kar dega aur scoring
        #    ka poora matlab khatam.
        spec = ACTIONS[f["correct_action"]]
        assert spec.purpose == "correct", (
            f"{fid} ka correct_action {f['correct_action']!r} allowlist mein "
            f"purpose={spec.purpose!r} hai — correct hona chahiye"
        )

        # Target = wo service jispe fix chalega. Ye layer 2 se pass hona chahiye,
        # warna sahi jawab bhi broker se reject ho jaayega.
        target = f["correct_target"]
        assert target in ALLOWED_SERVICES, (
            f"{fid} ka correct_target {target!r} ALLOWED_SERVICES mein nahi hai — "
            f"sahi jawab layer 2 se reject ho jaayega"
        )
        assert target in spec.services, (
            f"{fid}: {f['correct_action']} {target!r} pe allowed nahi hai "
            f"(allowed: {sorted(spec.services)}) — sahi jawab chal hi nahi sakta"
        )


def test_the_correct_answer_actually_passes_all_five_layers() -> None:
    """🔥 Sabse strong coverage check: har fault ka sahi jawab **broker se guzar
    kar** dikhaya jaaye.

    §8 ka pichhla test sirf membership check karta hai. Ye asli call karta hai —
    kyunki `set_db_setting` jaisa action ko `key`/`value` bhi chahiye, aur wo
    layer 3 se pass hone chahiye. Ek fault jiska sahi jawab allowlist mein hai
    par uske params kabhi legal nahi ban sakte, wo bhi unfixable hai.
    """
    extra: Mapping[str, dict[str, Any]] = {
        # F4 ka sahi jawab: pool ke liye kaafi connections. POOL_MAX=10 +
        # superuser reserve 3 → 200 aaram se kaafi hai.
        "set_db_setting": {"key": "max_connections", "value": 200},
        "clear_directory": {"path": "/tmp/shopapp-cache"},
    }
    for f in _fault_ground_truth():
        for action in (f["correct_action"], *f["also_acceptable"]):
            params: dict[str, Any] = {"service": f["correct_target"]}
            params.update(extra.get(action, {}))
            out = validate(
                action,
                params,
                approval=_approved(action, params),
                incident_id=INCIDENT,
                attempt=0,
            )
            assert out["service"] == f["correct_target"], out


# ═════════════════════════════════════════════════════════════════════════════
# §9 · Answer key ka doosra darwaza — `fixes` aur `purpose`
# ═════════════════════════════════════════════════════════════════════════════
#
# 🔴 CLAUDE.md §3 kehta hai: "nothing under oncall/ may import from chaos/ or
#    read correct_action / is_relevant". `test_no_shell.py` wo import-level rule
#    enforce karta hai. Lekin ek **doosra** raasta hai jo us rule se bachta hai:
#
#        ACTIONS["restart_service"].fixes     → ("F1",)
#        ACTIONS["stop_service"].purpose      → "distractor"
#
#    Ye dono field `allowlist.py` mein hain — `oncall/` ke andar, `chaos/` se
#    kuch bhi import kiye bina. Aur ye answer key hi hain:
#
#      • `fixes` seedha bolta hai kaunsa action kis fault ka jawab hai.
#      • `purpose` teen options ko "distractor" label kar deta hai — 9 mein se
#        3 chhaant dena, bina socha, muft.
#
#    Asli khatra kaal ka hai: `oncall/llm/prompts.py` abhi likhi nahi gayi. Jab
#    likhi jaayegi, sabse natural code hoga "har action ka naam aur description
#    prompt mein daal do" — ek loop jo `ActionSpec` ke saare field dump kar de.
#    Us din `fixes=("F4",)` prompt mein chala jaayega. Aur agar runbook ids bhi
#    fault ids se match karte hain (jo plan hai), toh model dono jod kar answer
#    key bana lega — aur eval ka number 🔴 jhooth ho jaayega, bina kisi ke
#    galat niyat ke.
#
#    Isliye guard **aaj** likha ja raha hai, prompts.py se pehle. Ek check jo
#    hole banne se pehle maujood ho, wo hole banne hi nahi deta.
#
#    ✅ Verified 2026-08-22: aaj `.purpose` sirf `allowlist.py` mein 4 jagah
#       padha jaata hai (import-time invariant + `remediating_actions()` /
#       `distractors()` helpers), aur `.fixes` kahin bhi nahi. Guard green hai.

#: Wo fields jo bata dete hain ki *sahi jawab kya hai*. Inhe sirf `allowlist.py`
#: define/padh sakti hai; agent ke request path mein ye nahi jaane chahiye.
ANSWER_KEY_FIELDS: frozenset[str] = frozenset({"fixes", "purpose"})

#: Wo helpers jo answer key ko list kar dete hain — eval/docs ke liye hain,
#: prompt ke liye nahi.
ANSWER_KEY_HELPERS: frozenset[str] = frozenset({"remediating_actions", "distractors"})

#: Ek hi file ko chhoot hai: wahi jo in fields ki authority hai.
_ANSWER_KEY_HOME = "oncall/actions/allowlist.py"


def _oncall_py_files() -> tuple[Path, ...]:
    return tuple(sorted((REPO_ROOT / "oncall").rglob("*.py")))


def _answer_key_reads(files: tuple[Path, ...], root: Path | None = None) -> list[str]:
    """Un jagahon ki list jahan answer-key field/helper padha ja raha hai.

    Do node types dekhte hain:
      `ast.Attribute` — `spec.fixes`, `ACTIONS[x].purpose`
      `ast.Call`      — `remediating_actions()`, `distractors()`

    🔴 Keyword argument (`ActionSpec(fixes=("F1",))`) ya field annotation
       (`fixes: tuple[str, ...]`) ko **nahi** ginte — wo define karna hai, padhna
       nahi. AST mein ye alag node types hain, isliye farq karna aasan hai;
       grep se ye farq karna namumkin hota.

    `root` param sirf negative control ke liye hai: wo ek temp directory ko root
    maan kar guard ko chala kar dekhta hai ki guard asal mein leak pakadta hai.
    (Global mutate karne se behtar — ek argument test-only rehta hai, ek global
    poore module ko chhoo leta hai.)
    """
    base = REPO_ROOT if root is None else root
    out: list[str] = []
    for path in files:
        rel = path.relative_to(base).as_posix()
        if rel == _ANSWER_KEY_HOME:
            continue
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Attribute) and node.attr in ANSWER_KEY_FIELDS:
                out.append(f"{rel}:{node.lineno} → .{node.attr}")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ANSWER_KEY_HELPERS
            ):
                out.append(f"{rel}:{node.lineno} → {node.func.id}()")
            elif isinstance(node, ast.ImportFrom) and node.module == "oncall.actions.allowlist":
                for alias in node.names:
                    if alias.name in ANSWER_KEY_HELPERS:
                        out.append(f"{rel}:{node.lineno} → import {alias.name}")
    return out


def test_answer_key_fields_never_leave_the_allowlist() -> None:
    """`fixes` / `purpose` sirf `allowlist.py` mein. Kahin aur = answer key leak."""
    files = _oncall_py_files()
    assert len(files) >= 10, f"sirf {len(files)} file scan hui — glob toota hai"
    leaks = _answer_key_reads(files)
    assert leaks == [], (
        "🔴 Answer key oncall/ ke andar padhi ja rahi hai:\n  "
        + "\n  ".join(leaks)
        + "\n\n`fixes` batata hai kaunsa action kis fault ka jawab hai; `purpose` "
        "distractors ko label kar deta hai. Dono prompt mein pahunche toh eval "
        "ka number jhooth ho jaata hai (CLAUDE.md §3)."
    )


# ═════════════════════════════════════════════════════════════════════════════
# §10 · Negative control — kya ye parsers asal mein kuch padh rahe hain?
# ═════════════════════════════════════════════════════════════════════════════
#
# 🔥 Sabse khatarnaak test wo nahi hota jo fail ho jaata hai. Sabse khatarnaak
#    wo hota hai jo **0 cheezein padh kar pass** ho jaata hai.
#
#    Socho `_dict_keys_of()` mein ek typo aa jaaye — `"HANDLERS"` ki jagah
#    `"HANDLER"`. Function `AssertionError` phenkega… theek. Par socho wo
#    chupchaap `()` return kare (jaise `continue` ki jagah `break` ho jaaye):
#    phir `set(()) == allow` fail hoga, toh wo bhi pakda jaayega.
#
#    Asli chhupa hua case: `_answer_key_reads()`. Wo ek **khaali list** return
#    karta hai jab sab theek ho — aur khaali list wahi hai jo ek toota hua
#    parser bhi deta hai. `assert leaks == []` dono haalaton mein green hai.
#    Ek guard jo kabhi kuch nahi dhoondhta wo guard nahi, sirf ek dilasa hai.
#
#    Isliye: temp mein jaan-boojh kar galat file plant karo, aur check karo ki
#    parser use **pakadta** hai. Ye test tabhi pass hota hai jab guard asal mein
#    kaam kar raha ho.
#
# 🔴 `tmp_path=None` signature `test_no_shell.py` se copy hai: pytest ise
#    fixture bhar deta hai, aur `python3 tests/test_broker.py` (bina pytest)
#    `tempfile` fallback use karta hai. Ek test jo sirf pytest ke saath chale,
#    wo aaj chalta hi nahi — aur jo aaj na chale, wo kal sad jaata hai.


def test_guards_catch_planted_drift(tmp_path: Path | None = None) -> None:
    if tmp_path is None:  # standalone run — pytest fixture available nahi
        import tempfile

        tmp_path = Path(tempfile.mkdtemp(prefix="oncall-negctl-"))

    # ── 1. HANDLERS parser ────────────────────────────────────────────────
    planted = tmp_path / "fake_handlers.py"
    planted.write_text(
        '"""HANDLERS mein "restart_service" likha hai — comment mein."""\n'
        "HANDLERS = {\n"
        '    "restart_service": None,\n'
        '    "rm_rf_slash": None,\n'  # 🔴 allowlist mein nahi
        "}\n",
        encoding="utf-8",
    )
    got = _dict_keys_of(planted, "HANDLERS")
    assert got == ("restart_service", "rm_rf_slash"), f"parser ne padha: {got}"
    assert set(got) != set(ACTION_NAMES), "planted drift pakda nahi gaya"

    # ── 2. Literal parser ─────────────────────────────────────────────────
    planted = tmp_path / "fake_schema.py"
    planted.write_text(
        "from typing import Literal\n"
        'ActionName = Literal["restart_service", "drop_database"]\n',
        encoding="utf-8",
    )
    got = _literal_members_of(planted, "ActionName")
    assert got == ("restart_service", "drop_database"), f"parser ne padha: {got}"

    # ── 3. Fault ground-truth parser ──────────────────────────────────────
    planted = tmp_path / "fake_faults.py"
    planted.write_text(
        "FAULTS = {\n"
        '    "F9": Fault(\n'
        '        id="F9",\n'
        '        correct_action="pray_and_hope",\n'  # 🔴 allowlist mein nahi
        '        correct_target="mars",\n'  # 🔴 ALLOWED_SERVICES mein nahi
        '        also_acceptable=("sacrifice_a_goat",),\n'
        "    ),\n"
        "}\n",
        encoding="utf-8",
    )
    rows = _fault_ground_truth(planted)
    assert len(rows) == 1, rows
    assert rows[0]["id"] == "F9", rows[0]
    assert rows[0]["correct_action"] == "pray_and_hope", rows[0]
    assert rows[0]["correct_target"] == "mars", rows[0]
    assert rows[0]["also_acceptable"] == ("sacrifice_a_goat",), rows[0]
    assert rows[0]["correct_action"] not in ACTIONS, "planted fault pakda nahi gaya"
    assert rows[0]["correct_target"] not in ALLOWED_SERVICES

    # ── 4. Answer-key leak guard — sabse zaruri negative control ─────────
    # Ye guard normally khaali list deta hai, aur khaali list ek toote hue
    # parser ka bhi jawab hai. Toh ek asli leak plant karke check karo.
    leaky = tmp_path / "oncall" / "llm" / "prompts.py"
    leaky.parent.mkdir(parents=True, exist_ok=True)
    leaky.write_text(
        "from oncall.actions.allowlist import ACTIONS, distractors\n"
        "\n"
        "def build_menu() -> str:\n"
        "    lines = []\n"
        "    for spec in ACTIONS.values():\n"
        "        # 🔴 yahi wo leak hai jo asli code mein aa sakta hai\n"
        "        lines.append(f'{spec.name} fixes {spec.fixes} ({spec.purpose})')\n"
        "    return '\\n'.join(lines) + str(distractors())\n",
        encoding="utf-8",
    )
    found = _answer_key_reads((leaky,), root=tmp_path)
    joined = " | ".join(found)
    assert any(".fixes" in f for f in found), joined
    assert any(".purpose" in f for f in found), joined
    assert any("distractors()" in f for f in found), joined
    assert any("import distractors" in f for f in found), joined


def test_runner_lists_every_test_in_this_file() -> None:
    """🔴 Ek test jo runner ki list mein nahi hai, wo standalone chalta hi nahi.

    Ye is file ka apna drift problem hai, aur bilkul CLAUDE.md §3 wali shakl ka:
    do jagah ek hi list likhi hui hai — `def test_*` (definitions) aur `_checks`
    (runner ki tuple). Naya test likh kar `_checks` mein add karna bhool jaana
    sabse aasan galti hai, aur uska nateeja sabse chupa hua: `python3
    tests/test_broker.py` **green** dikhata hai jabki ek check chala hi nahi.

    pytest ise nahi pakadta — wo `test_*` khud dhoondh leta hai. Isliye ye check
    zaruri hai: pytest aur standalone runner ko ek hi list dekhni chahiye.

    Khud ko bhi ginta hai — ye function bhi `test_` se shuru hota hai, toh isko
    bhi `_checks` mein hona padega.
    """
    tree = _parse(Path(__file__).resolve())

    defined = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }

    listed: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        tgt = node.targets[0]
        if not isinstance(tgt, ast.Name) or tgt.id != "_checks":
            continue
        if isinstance(node.value, ast.Tuple):
            listed = {e.id for e in node.value.elts if isinstance(e, ast.Name)}
        break

    assert listed, "`_checks = (...)` tuple nahi mila — runner block gayab hai"
    assert defined, "ek bhi `def test_*` nahi mila — parser toota hai"
    assert defined == listed, (
        "runner list aur test definitions match nahi karti:\n"
        f"  define hua par _checks mein nahi : {sorted(defined - listed)}\n"
        f"  _checks mein hai par define nahi : {sorted(listed - defined)}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# Runner — `python3 tests/test_broker.py` bina pytest ke chalta hai
# ═════════════════════════════════════════════════════════════════════════════
#
# 🔴 Ye block `test_no_shell.py` se copy hai, jaan-boojh kar. Wajah: is machine
#    pe pytest install nahi hai (network `pip install` allow nahi karta), aur
#    security boundary ka test **aaj** chalna chahiye. Ek test jo sirf
#    `make install` ke baad chale, wo pehle din se hi ek IOU hai.
#
#    Dono raste kaam karte hain:
#        python3 tests/test_broker.py       # aaj
#        python3 -m pytest tests/ -q        # make install ke baad

if __name__ == "__main__":
    _checks = (
        # §0 — happy path pehle. Sabse zaruri test yahi hai.
        test_happy_path_runs,
        test_every_action_has_a_reachable_happy_path,
        # §1 — layer 1: naam
        test_layer1_unknown_name,
        test_layer1_case_and_whitespace_are_not_forgiven,
        test_layer1_non_string_action,
        # §2 — layer 2: blast radius
        test_layer2_agent_cannot_touch_its_own_brain,
        test_layer2_per_action_service_scope,
        test_layer2_escalate_must_not_carry_a_service,
        test_layer2_missing_or_bad_type_service,
        # §3 — layer 3: params
        test_layer3_exact_key_set,
        test_layer3_path_is_exact_membership_not_prefix,
        test_layer3_db_key_allowlist,
        test_layer3_value_range,
        test_layer3_the_bool_trap,
        test_layer3_no_silent_casts,
        test_layer3_table_allowlist,
        test_layer3_reason_must_say_something,
        # §4 — layer 4: approval (TOCTOU)
        test_layer4_only_approved_runs,
        test_layer4_approval_belongs_to_this_incident_and_attempt,
        test_layer4_action_swapped_after_approval,
        test_layer4_params_swapped_after_approval,
        test_layer4_compares_meaning_not_spelling,
        test_layer4_unrunnable_approved_row_is_layer4_not_layer3,
        # §5 — layer 5: budget
        test_layer5_budget_is_three_attempts,
        test_layer5_runs_before_layer4,
        test_layer5_non_int_attempt,
        # §6 — execute() ka contract
        test_log_fires_before_the_handler_runs,
        test_rejection_is_logged_too,
        test_rejected_action_never_reaches_a_handler,
        test_security_arguments_have_no_default,
        test_execute_only_accepts_str_and_mapping_not_a_proposal_object,
        # §7–§9 — chaar jagah, taxonomy, answer key
        test_four_places_agree,
        test_every_fault_has_a_correct_action_in_the_allowlist,
        test_the_correct_answer_actually_passes_all_five_layers,
        test_answer_key_fields_never_leave_the_allowlist,
        # §10 — negative control. Ye fail ho toh upar ke saare check shaq ke daayre mein hain.
        test_guards_catch_planted_drift,
        test_runner_lists_every_test_in_this_file,
    )
    _failed = 0
    print(f"{len(ACTION_NAMES)} allowed actions · {len(_checks)} checks\n")
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
    print(f"saare {len(_checks)} broker check pass hue")
