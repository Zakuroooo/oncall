"""State machine ka CONTRACT — states, allowed edges, aur ek guard.

────────────────────────────────────────────────────────────────────────────
🔴 Ye file CODE hai, table nahi — aur wahi iska poora point hai
────────────────────────────────────────────────────────────────────────────
`LOGIC.md` §4 ki transition table yahan ek dict ban jaati hai. Koi bhi jump jo
is dict mein nahi hai, `assert_transition()` use **exception** ke saath rok deta
hai. Iske bina `advance_state(iid, frm="VERIFYING", to="PROPOSING")` jaisi galti
chup-chaap likh jaati — aur incident ka attempt count phir kisi ke paas nahi
hoti.

Do alag jagah state names likhe hain, jaan-boojh kar:

    `stack/oncalldb/init.sql:59`  → CHECK constraint (database ka bachaav)
    `oncall/db/queries.py`        → `STATES` tuple (process ke andar validation)
    ye file                       → `State` enum + edges (policy)

Teen copy drift ho sakti hain — isliye drift ko TEST pakadta hai:
`tests/test_states.py` teeno ko compare karta hai. Ek jagah badla, doosri bhooli
→ test lal.

🔴 Terminal states se bahar ka KOI edge nahi hai. `RECOVERED`/`ESCALATED` ke
   baad worker us incident ko kabhi nahi chhedta — agar koi din aisa code likha
   jo terminal se aage jaane ki koshish kare, yahan exception milega, DB mein
   chup-chaap row nahi.
"""

from __future__ import annotations

from enum import Enum


class State(str, Enum):
    """Incident ki 10 awasthaayein. `str` mixin isliye ki JSON/events payload
    mein seedha `state.value` ke bina bhi readable rahe."""

    DETECTED = "DETECTED"
    COLLECTING = "COLLECTING"
    DIAGNOSING = "DIAGNOSING"
    RETRIEVING = "RETRIEVING"
    PROPOSING = "PROPOSING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RECOVERED = "RECOVERED"
    ESCALATED = "ESCALATED"


#: Janm — transition NAHI hai (`LOGIC.md` §4 ki ginti: 16 rows, 15 edges).
#: Incident create hone ka raasta `INSERT` hai, ye dict se nahi guzarta.
BIRTH_STATE: State = State.DETECTED

#: Sirf ye do final hain. Baaki sab resumable — process mar jaaye toh naya
#: worker wahin se uthata hai jahan purana chhod kar gaya tha.
TERMINAL_STATES: frozenset[State] = frozenset({State.RECOVERED, State.ESCALATED})

# ─────────────────────────────────────────────────────────────────────────────
# 🔥 ALLOWED — poora policy yahi ek dict hai
# ─────────────────────────────────────────────────────────────────────────────
#
# Do edges jinpe sabse zyada sawaal hota hai, unka jawab yahan comment mein:
#
# 1. VERIFYING → COLLECTING (retry), PROPOSING nahi (D19). Failed fix ne
#    duniya BADAL di — naya error padhna zaruri hai, purana diagnosis stale
#    ho gaya. Naive retry wahi context dobara bhejta hai; ye system ko
#    dobara observe karta hai.
#
# 2. AWAITING_APPROVAL → EXECUTING sirf approval ke baad, aur → ESCALATED
#    sirf rejection pe. Ye edges `api/routes_approve.py` chalata hai — worker
#    inko kabhi khud se nahi chalata, warna human gate bypass ho jaata.
ALLOWED: dict[State, frozenset[State]] = {
    State.DETECTED: frozenset({State.COLLECTING}),
    State.COLLECTING: frozenset({State.DIAGNOSING}),
    # DIAGNOSING ka 3ra edge — ESCALATED — JSON 2x fail hone pe (R14).
    State.DIAGNOSING: frozenset({State.RETRIEVING, State.PROPOSING, State.ESCALATED}),
    # RETRIEVING → PROPOSING tab bhi jab 0 runbooks milein — "kuch nahi mila"
    # ek valid retrieval result hai, error nahi (ablation arm B isko skip
    # bhi karta hai: DIAGNOSING → PROPOSING seedha).
    #
    # 🔴 RETRIEVING → ESCALATED, added 2026-08-27 after an end-to-end run.
    #
    #    `loop.py:_step_retrieving` has ALWAYS escalated on
    #    `EmbedUnavailableError`, and for a good documented reason: carrying on
    #    with "0 runbooks" would let an incident labelled `rag_enabled=true`
    #    actually run without RAG, which silently corrupts the N3 ablation.
    #
    #    But this edge was never added here, so that path raised
    #    `IllegalTransitionError` instead. The job retried three times, went to
    #    `failed`, and the incident sat in RETRIEVING **forever** — no verdict,
    #    no escalation, no error anyone would see without reading the worker log.
    #
    #    Found by injecting F4 and watching it, not by reading code: every test
    #    passed, because the tests asserted the edge list matched itself. This
    #    is the gap between "the state machine is consistent" and "the state
    #    machine matches what the code does".
    State.RETRIEVING: frozenset({State.PROPOSING, State.ESCALATED}),
    # PROPOSING → ESCALATED = agent ne KHUD escalate action chuna (D20,
    # voluntary). Honest "mujhe nahi pata" ko first-class banata hai.
    State.PROPOSING: frozenset({State.AWAITING_APPROVAL, State.ESCALATED}),
    State.AWAITING_APPROVAL: frozenset({State.EXECUTING, State.ESCALATED}),
    # EXECUTING → VERIFYING exit code kuch bhi ho — exit code proxy hai,
    # oracle ground truth (D1). Action fail bhi hui aur service theek bhi
    # ho sakti hai; faisla VERIFYING pe hota hai.
    State.EXECUTING: frozenset({State.VERIFYING, State.ESCALATED}),
    State.VERIFYING: frozenset({State.RECOVERED, State.COLLECTING, State.ESCALATED}),
    State.RECOVERED: frozenset(),
    State.ESCALATED: frozenset(),
}


class IllegalTransitionError(ValueError):
    """Ek jump jo `ALLOWED` mein nahi hai. Programming error hai ye — data
    problem nahi — isliye exception, silent no-op nahi."""

    def __init__(self, frm: State | str, to: State | str) -> None:
        self.frm = frm
        self.to = to
        super().__init__(f"Illegal transition {frm!s} -> {to!s} — ALLOWED dict dekho")


def _coerce(state: State | str) -> State:
    """String ya State → State. Typo yahan marta hai, DB CHECK se pehle."""
    if isinstance(state, State):
        return state
    try:
        return State(str(state))
    except ValueError:
        raise IllegalTransitionError(state, "(unknown state)") from None


def assert_transition(frm: State | str, to: State | str) -> tuple[State, State]:
    """Edge legal hai? `(frm, to)` normalized wapas, warna raise.

    Returns isliye hain ki caller ko dobara enum lookup na karna pade —
    aur test normalize hone ka behaviour bhi assert kar sake."""
    f = _coerce(frm)
    t = _coerce(to)
    if t not in ALLOWED[f]:
        raise IllegalTransitionError(f, t)
    return f, t


def is_terminal(state: State | str) -> bool:
    """Terminal pe pahunch chuka? Worker loop isse poochh kar rukta hai."""
    return _coerce(state) in TERMINAL_STATES
