"""State machine ke tests — policy dict sach bol raha hai ya nahi.

Chalta hai dono tareeke se (repo convention):
    .venv/bin/python -m pytest tests/test_states.py -q
    .venv/bin/python tests/test_states.py

Kya check hota hai:
  §1  ALLOWED mein theek 16 edges hain (`LOGIC.md` §4 ki ginti)
  §2  har legal edge assert_transition se guzarti hai
  §3  har ILLEGAL jump exception deti hai — khaas kar do dangerous wali:
      AWAITING_APPROVAL → EXECUTING bina approval ke code se koi rasta nahi
      VERIFYING → PROPOSING (retry kabhi PROPOSING pe wapas nahi jaata, D19)
  §4  terminal states se bahar ka koi edge nahi
  §5  teen jagah ki state lists ek-doosre se match karti hain:
      ye file ka enum ↔ db/queries.py STATES ↔ init.sql ka CHECK constraint

§5 hi wo test hai jo drift pakadta hai — `LOGIC.md` §4 ka poora point.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    # Standalone run (`python tests/test_states.py`) mein sys.path[0] = tests/
    # hota hai, repo root nahi. Iske bina `import oncall` fail hota hai.
    sys.path.insert(0, str(REPO_ROOT))

from oncall.agent.states import (  # noqa: E402
    ALLOWED,
    BIRTH_STATE,
    TERMINAL_STATES,
    IllegalTransitionError,
    State,
    assert_transition,
    is_terminal,
)

SCHEMA_PATH = REPO_ROOT / "stack" / "oncalldb" / "init.sql"


# ═════════════════════════════════════════════════════════════════════════════
# §5 ka helper — init.sql se CHECK constraint ki literals nikaalo
# ═════════════════════════════════════════════════════════════════════════════
def _states_in_schema_check() -> set[str]:
    """`incidents_state_valid` CHECK ke andar ke saare 'QUOTED' strings.

    Regex-based hai jaan-boojh kar: SQL parse karne se sasta aur is file ke
    liye kaafi — constraint block chhota aur stable hai. Agar block hil gaya,
    neeche wala count-check (== 10) loudly fail karega, chup nahi rahega."""
    text = SCHEMA_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"incidents_state_valid\s+CHECK\s*\((.*?)\)\s*,?\s*\n", text, re.DOTALL
    )
    if m is None:
        raise AssertionError("init.sql mein incidents_state_valid CHECK nahi mila")
    return set(re.findall(r"'([A-Z_]+)'", m.group(1)))


def _edge_count() -> int:
    """Dict ki total edges — `len(ALLOWED)` NAHI (wo sources ginti hai)."""
    return sum(len(targets) for targets in ALLOWED.values())


# ── §1: ginti ────────────────────────────────────────────────────────────────
def test_exactly_16_edges() -> None:
    # LOGIC.md §4: 17 table-rows, 16 dict-edges — farq janm (BIRTH) ka hai.
    #
    # 🔴 15 → 16 on 2026-08-27: RETRIEVING → ESCALATED. `loop.py` had always
    #    taken that path on an embed-service outage; ALLOWED never listed it,
    #    so the incident wedged in RETRIEVING instead of escalating honestly.
    #    See the comment on that edge in `states.py`.
    assert _edge_count() == 16, f"edges {_edge_count()} hain, 16 hone chahiye"


def test_all_10_states_present() -> None:
    assert len(State) == 10
    assert set(ALLOWED) == set(State), "ALLOWED dict aur State enum alag hain"


def test_terminal_states_are_exactly_two() -> None:
    assert TERMINAL_STATES == {State.RECOVERED, State.ESCALATED}


# ── §2: legal edges guzartin hain ───────────────────────────────────────────
def test_every_dict_edge_passes_assert_transition() -> None:
    for frm, targets in ALLOWED.items():
        for to in targets:
            got = assert_transition(frm, to)  # raise na hua = pass
            assert got == (frm, to)


def test_string_names_accepted_and_normalized() -> None:
    # Caller (loop.py) DB se plain strings laata hai — API ko dono swikaar hai.
    frm, to = assert_transition("DETECTED", "COLLECTING")
    assert frm is State.DETECTED and to is State.COLLECTING


def test_birth_state_is_detected() -> None:
    assert BIRTH_STATE is State.DETECTED


# ── §3: illegal jumps ───────────────────────────────────────────────────────
def test_illegal_jump_raises_with_layered_message() -> None:
    try:
        assert_transition(State.VERIFYING, State.PROPOSING)
    except IllegalTransitionError as exc:
        assert exc.frm == State.VERIFYING
        assert exc.to == State.PROPOSING
        assert "VERIFYING" in str(exc) and "PROPOSING" in str(exc)
    else:
        raise AssertionError("VERIFYING → PROPOSING guzar gayi (D19 toota)")


def test_retry_never_returns_to_proposing() -> None:
    # 🔴 D19 ka enforcement: fail hue toh COLLECTING pe wapas — naya error,
    # purana diagnosis stale. Ye edge kabhi add mat karna.
    assert State.PROPOSING not in ALLOWED[State.VERIFYING]
    assert State.COLLECTING in ALLOWED[State.VERIFYING]


def test_approval_gate_has_no_bypass() -> None:
    # EXECUTING sirf AWAITING_APPROVAL se pahuncha ja sakta hai — aur wo edge
    # sirf approve endpoint chalata hai. Koi doosra state EXECUTING tak nahi
    # jaanta, toh human gate bypass karna impossible hai.
    reaching_executing = {
        frm for frm, targets in ALLOWED.items() if State.EXECUTING in targets
    }
    assert reaching_executing == {State.AWAITING_APPROVAL}


def test_unknown_state_name_rejected() -> None:
    try:
        assert_transition("RETRIEVEING", "PROPOSING")  # jaan-boojh kar typo
    except IllegalTransitionError:
        pass
    else:
        raise AssertionError("typo'd state guzar gayi")


# ── §4: terminal se bahar kuch nahi ─────────────────────────────────────────
def test_terminal_states_have_no_outgoing_edges() -> None:
    for t in TERMINAL_STATES:
        assert len(ALLOWED[t]) == 0, f"{t} terminal hai par edges: {ALLOWED[t]}"


def test_is_terminal() -> None:
    assert is_terminal(State.RECOVERED) and is_terminal("ESCALATED")
    assert not is_terminal(State.VERIFYING)


# ── §5: teen copies ek hain ─────────────────────────────────────────────────
def test_states_match_db_queries_tuple() -> None:
    # db/queries.py apni list init.sql se copy karta hai; ye enum usse compare
    # hota hai. Import psycopg ke BINA bhi safe hai (queries.py optional-import
    # pattern use karta hai).
    from oncall.db.queries import STATES as Q_STATES

    assert {s.value for s in State} == set(Q_STATES)
    assert len(Q_STATES) == 10


def test_states_match_init_sql_check_constraint() -> None:
    schema_states = _states_in_schema_check()
    assert len(schema_states) == 10, (
        f"init.sql CHECK mein {len(schema_states)} states mili — parser ya "
        "schema drift ho gaya hai"
    )
    assert schema_states == {s.value for s in State}


# ── Guard: runner mein har test aata hai? ───────────────────────────────────
def test_runner_lists_every_test_in_this_file() -> None:
    here = Path(__file__).read_text(encoding="utf-8")
    defined = set(re.findall(r"^def (test_\w+)\(", here, re.MULTILINE))
    listed = {fn.__name__ for fn in _CHECKS}
    missing = defined - listed - {"test_runner_lists_every_test_in_this_file"}
    assert not missing, f"_CHECKS tuple mein ye test gayab hain: {sorted(missing)}"
    duplicates = len(listed) != len(_CHECKS)
    assert not duplicates, "_CHECKS mein koi test dobara likha gaya hai"


#: Module-level isliye ki guard test bhi ise padh sake (`test_seed.py` wala
#: pattern — `_checks` sirf `__main__` mein hota toh pytest ke neeche guard ka
#: NameError aata).
_CHECKS: tuple = (
    test_exactly_16_edges,
    test_all_10_states_present,
    test_terminal_states_are_exactly_two,
    test_every_dict_edge_passes_assert_transition,
    test_string_names_accepted_and_normalized,
    test_birth_state_is_detected,
    test_illegal_jump_raises_with_layered_message,
    test_retry_never_returns_to_proposing,
    test_approval_gate_has_no_bypass,
    test_unknown_state_name_rejected,
    test_terminal_states_have_no_outgoing_edges,
    test_is_terminal,
    test_states_match_db_queries_tuple,
    test_states_match_init_sql_check_constraint,
    test_runner_lists_every_test_in_this_file,
)

if __name__ == "__main__":
    _failed = 0
    for _fn in _CHECKS:
        try:
            _fn()
        except AssertionError as _exc:
            _failed += 1
            print(f"FAIL  {_fn.__name__}: {_exc}")
        else:
            print(f"ok    {_fn.__name__}")
    if _failed:
        raise SystemExit(f"{_failed}/{len(_CHECKS)} check fail hue")
    print(f"\nsab {len(_CHECKS)} check paas")
