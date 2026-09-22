"""LLM ka output kaise "shape" mein aata hai — do Pydantic models, bas.

🔒 Ye file CLAUDE.md §3 ki chaar jagahon mein **teesri** hai:

    allowlist.py   → authority (naam + kya legal hai)
    handlers.py    → har naam ka asli Docker call
    llm/schema.py  → ye file: `Literal[...]` taaki galat naam parse hi na ho
    test_broker.py → adversarial tests

────────────────────────────────────────────────────────────────────────────
Pehle concept: "structured output" ka matlab kya hai (CLAUDE.md §1)
────────────────────────────────────────────────────────────────────────────

LLM text generate karta hai. Text ka koi shape nahi hota. Agar hum use
seedha parse karein, toh teen cheezein normal hain:

    1. JSON ke aage-peeche baatein  →  "Sure! Here's the action:\\n```json..."
    2. Field ka naam badal jaana    →  "act" instead of "action"
    3. Extra fields ghus jaana      →  {"action": ..., "force": true}

Teesra sabse khatarnaak hai. Agar hum `dict` seedha handler ko `**kwargs`
bana kar de dein, toh ek extra key ek aisa argument ban sakti hai jo humne
kabhi socha hi nahi. Isliye `extra="forbid"` — unknown field = poora reject.

Pydantic ka kaam yahan validation hai, "cleaning" nahi. Wo galat input ko
theek karne ki koshish nahi karta; wo use **mana** kar deta hai. Fail-closed.

Rejected alternative: `json.loads()` + haath se `if "action" not in d`.
Wo chalta hai, lekin har naya field ek naya `if` maangta hai, aur ek bhoola
hua `if` = ek chhupa hua hole. Pydantic mein shape *declaration* hai, code
nahi — aur declaration ko padh kar audit kiya ja sakta hai.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from oncall.actions.allowlist import ACTION_NAMES

# ─────────────────────────────────────────────────────────────────────────────
# 🔴 Ye list yahan DOBARA likhi gayi hai. Jaan-boojh kar. Wajah ye hai:
# ─────────────────────────────────────────────────────────────────────────────
#
# `Literal[...]` ko **static** hona padta hai. `Literal[*ACTION_NAMES]` Python
# mein legal nahi hai, aur agar hum runtime pe banayein bhi, toh type checker
# (mypy/pyright) use padh nahi sakta — usse sirf `str` dikhega. Toh do options
# thay:
#
#   (a) Literal hata do, `action: str` rakho, aur naam broker mein check karo.
#       → Tab galat naam poore diagnosis + retrieval ke baad pakda jaata,
#         aur Pydantic ka error message bekaar ho jaata.
#   (b) List dobara likho, aur duplication ko **khud ko check karne wala**
#       banao.
#
# (b) chuna. Duplication ka khatra drift hai — toh neeche wala check import
# time pe hi phat jaata hai agar dono lists alag ho jaayein. Ek duplicate jo
# apne aap ko verify karta hai, wo duplicate nahi — wo do-tarfa assertion hai.
ActionName = Literal[
    "restart_service",
    "start_service",
    "clear_directory",
    "kill_blocking_queries",
    "set_db_setting",
    "stop_service",
    "flush_cache",
    "vacuum_table",
    "escalate",
]

# 🔴 `assert` nahi — `python -O` use hata deta hai (allowlist.py mein wahi baat).
_literal_names = get_args(ActionName)
if set(_literal_names) != set(ACTION_NAMES):  # pragma: no cover - import-time
    raise RuntimeError(
        "schema.py ka Literal allowlist.py se drift ho gaya.\n"
        f"  Literal mein extra : {sorted(set(_literal_names) - set(ACTION_NAMES))}\n"
        f"  Literal mein gayab : {sorted(set(ACTION_NAMES) - set(_literal_names))}\n"
        "CLAUDE.md §3: chaar jagah edit karni hoti hain, teen kaafi nahi."
    )
if len(_literal_names) != len(ACTION_NAMES):  # pragma: no cover - import-time
    raise RuntimeError("Literal mein duplicate naam hai")


# ─────────────────────────────────────────────────────────────────────────────
# LLM #1 ka output — diagnosis. Yahan koi action nahi hai, jaan-boojh kar.
# ─────────────────────────────────────────────────────────────────────────────


class Diagnosis(BaseModel):
    """LLM #1: "kya toota hai" — "kya karna hai" nahi.

    🔴 Do LLM calls kyun, ek nahi (D-series ka core design):

    Agar ek hi call se diagnosis aur action dono maangein, toh model apne
    action ko justify karne ke liye diagnosis ko mod deta hai. Ye insaan
    bhi karte hain — pehle jawab socho, phir wajah banao. Do calls mein
    LLM #1 ko allowlist **dikhayi hi nahi jaati**, toh wo "kaunsa action
    available hai" ke hisaab se symptom describe nahi kar sakta.

    Aur ek practical fayda: diagnosis text **retrieval ki query** hai.
    Agar diagnosis action se contaminated ho, toh retrieval bhi contaminated
    ho jaata — hum wahi runbook dhoondhne lagte jo action ko sahi thehraye.
    """

    model_config = ConfigDict(extra="forbid")

    #: Ek line mein kya toota hai. Yahi hybrid retrieval ki search query banti hai.
    summary: str = Field(min_length=10, max_length=300)

    #: Kaunsi service sabse zyada shaq ke daayre mein hai. Free string —
    #: allowlist yahan lagana galat hota, kyunki LLM #1 ko allowlist ka pata
    #: nahi hona chahiye. Broker ke Layer 2 mein service check hoti hai.
    suspected_service: str = Field(min_length=1, max_length=64)

    #: Logs ki wo exact lines jinpe ye diagnosis tika hai. 🔴 Ye field
    #: "citation" hai — iske bina model kuch bhi keh sakta hai aur hum check
    #: nahi kar sakte. Iske saath, ek galat diagnosis bhi *auditable* hai.
    evidence: list[str] = Field(min_length=1, max_length=5)

    #: 0.0–1.0. ⚠️ Ye LLM ka apna claim hai, measurement nahi — METRICS.md
    #: isse verdict ke against plot karta hai, verdict decide karne ke liye
    #: nahi. Model ka confidence kabhi evidence nahi hota (CLAUDE.md §0).
    confidence: float = Field(ge=0.0, le=1.0)


# ─────────────────────────────────────────────────────────────────────────────
# LLM #2 ka output — ek action. Yahi row insaan ko approve karne ko jaati hai.
# ─────────────────────────────────────────────────────────────────────────────


class ProposedAction(BaseModel):
    """LLM #2: allowlist mein se **ek** action, params ke saath.

    🔴 `params` ko `dict[str, str | int]` kyun rakha, per-action typed models
    (discriminated union) kyun nahi:

    Discriminated union zyada "correct" Pydantic hai, lekin wo security check
    ko **galat jagah** le jaata hai. Sequence dekho:

        LLM propose karta hai  →  Pydantic validate  →  DB row  →  INSAAN
        approve karta hai (minute? ghante?)  →  broker execute karta hai

    Approval ke baad execute hota hai, aur beech mein waqt guzarta hai. Agar
    poori validation sirf parse-time pe hoti, toh wo check **stale** ho jaati
    — allowlist badal sakti hai, row edit ho sakti hai. Isliye asli boundary
    broker ke 5 layers hain, jo **execute ke waqt** dobara check karte hain
    (TOCTOU: check aur use ke beech ka gap).

    Toh yahan ka kaam sirf ye hai: shape sahi ho, naam allowlist se ho, aur
    koi extra field na ho. Value-level validation broker ka kaam hai.
    Do jagah "poori" validation likhne se ek jagah dheeli reh jaati hai, aur
    aadmi maan leta hai doosri ne pakad liya hoga.
    """

    model_config = ConfigDict(extra="forbid")

    #: Sirf ye 9 naam parse honge. 10va naam Pydantic pe hi mar jaata hai —
    #: broker tak pahunchta hi nahi.
    action: ActionName

    #: Action-specific arguments. Bool jaan-boojh kar nahi hai: aaj koi action
    #: boolean nahi leta, aur `fsync = off` jaisi cheezein allowlist se bahar
    #: rakhi gayi hain (allowlist.py: ALLOWED_DB_KEYS).
    params: dict[str, str | int] = Field(default_factory=dict)

    #: 🔴 Ye field optional NAHI hai, aur ye UI decoration bhi nahi hai. Ye wo
    #: text hai jo insaan approval screen pe padhta hai. Agar model apni wajah
    #: na likh sake, toh insaan "approve" dabaane ke liye kuch nahi jaanta —
    #: aur phir approval gate ek rubber stamp ban jaata hai.
    reasoning: str = Field(min_length=10, max_length=500)

    #: Kaunsa runbook is action ke peeche tha. `None` = RAG ne kuch relevant
    #: nahi diya, ya RAG off tha (ablation N1). Isse hum "runbook ke bina
    #: accuracy" measure kar sakte hain.
    runbook_id: str | None = Field(default=None, max_length=64)
