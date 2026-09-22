"""Oracle ka falsifier test (D1) — naive vs sustained disagreement.

🔴 Ye test project ki THESIS check karta hai. Naam khud bolta hai kya hota:

    F4 (pool exhaustion, max_connections=5) inject ho → galat fix (restart)
    chal jaaye (deewar wahin khadi rehti hai) → tab:
      - NAIVE oracle (1 probe, no load):        PASS bolega ← false positive
      - SUSTAINED oracle (3 probes/30s/~20rps): FAIL bolega ← sahi

Agar sustained kabhi F4-restart pe PASS bolne lage, oracle kamzor ho gaya aur
N1 metric ka matlab khatam (`METRICS.md` §4 ka falsifier).

⚠️ LIVE STACK chahiye (docker up, shopapp healthy, chaos). Bina stack ke poora
module safely SKIP hota hai — skip jhoot nahi bolta, wajah bhi batata hai.
Chalane ka tareeka:
    make up && .venv/bin/python -m pytest tests/test_oracle.py -q -m "needs_docker"
    (~45 second lagega — 30s ka verification window hai, isliye `slow` bhi mark hai)

🔴 Order matter karta hai: naive test PEHLE chalta hai (idle service), sustained
   BAAD mein (load window). Isliye dono ek hi module-scoped fixture share karte
   hain aur file order jaan-boojh kar yahi hai.
"""

from __future__ import annotations

import asyncio
import importlib
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from oncall.config import settings  # noqa: E402


def _stack_up() -> bool:
    """Patient check: pichhle oracle-run ka heal shopapp ko restart karta hai,
    aur wo ~8s mein wapas aata hai. Turant `False` bolne se test galat-skip
    hota tha (2026-08-24 measured) — isliye chhota retry loop."""
    for _ in range(5):
        try:
            resp = httpx.get(settings.shopapp_health_url, timeout=3.0)
            if resp.status_code == 200:
                return True
        except Exception:  # noqa: BLE001 - daemon/container down = not up (yet)
            pass
        time.sleep(2.0)
    return False


pytestmark = [
    pytest.mark.needs_docker,
    pytest.mark.slow,
]

if not _stack_up():
    # 🔴 Module-level skip: bina stack ke ye tests CHAL NAHI SAKTE (fault
    # inject karna hai). Skip message khud batata hai kya karna hai — "0
    # passed" wala silent green nahi.
    pytest.skip(
        "live stack nahi hai (shopapp /health tak pahunch nahi). "
        "`make up` chalao phir ye test dobara chalao.",
        allow_module_level=True,
    )


@pytest.fixture(scope="module")
def f4_broken_with_wrong_fix():
    """F4 inject → GALAT fix (restart_service shopdb) apply → yield → heal.

    🔴 Galat fix JAAN-BOOJH KAR `restart_service` hai: wo F4 ko theek NAHI
       karta (ALTER SYSTEM ki value postgresql.auto.conf mein zinda rehti hai),
       sirf pool khaali karta hai — aur yahi false-positive machine banata hai.
    """
    faults = importlib.import_module("chaos.faults")
    fault = faults.FAULTS["F4"]
    from oncall.actions.handlers import restart_service

    fault.break_fn()
    restart_service(service="shopdb")

    # 🔴 Inject VERIFICATION (2026-08-24 raat ka flake-fix): ek baar `docker
    #    restart` recovery-slow tha aur test ne misleading "ORACLE BROKEN"
    #    diya jabki fault hi active nahi hua tha. Ab hum GROUND TRUTH padhte
    #    hain — `SHOW max_connections` — aur 5 na milne pe saaf wajah ke saath
    #    fail karte hain. (Ye test chaos ke bahar se DB padhta hai — agent
    #    path se koi lena-dena nahi.)
    import time as _t

    import httpx as _hx

    _deadline = _t.monotonic() + 20.0
    while True:
        try:
            out = subprocess.run(  # noqa: S603 - fixed argv list, test file (oncall/ ka shell-ban yahan lagtu nahi)
                [
                    "docker", "exec", "oncall-shopdb",
                    "psql", "-U", "shop", "-d", "shop", "-tAc",
                    "SHOW max_connections;",
                ],
                capture_output=True, text=True, timeout=10,
            )
            if out.returncode == 0 and out.stdout.strip() == "5":
                break
        except Exception:  # noqa: BLE001
            pass
        if _t.monotonic() > _deadline:
            pytest.fail(
                f"F4 inject verify fail: max_connections kabhi 5 nahi hui "
                f"(last={out.stdout.strip()!r}) — ALTER SYSTEM/restart race. "
                "Test environment bug hai, oracle ka nahi."
            )
        _t.sleep(1.0)

    # 🔴 Stable-BROKEN baseline ka intezaar: break_fn + wrong-fix handler MILKAR
    #    do shopdb restarts karte hain. Doosra restart pehle ke boot ke beech
    #    mein girta hai — aur naive probe kabhi-kabhi us recovery window mein
    #    503 khaa jaati thi (2026-08-24 measured flake). Ab hum wait karte
    #    hain jab tak system "stable-broken" na ho jaye: DB jawab de raha hai,
    #    shopapp /health wapas 200 (idle pe cap-5 se farq nahi padta), aur
    #    fault phir bhi active hai (cap=5 confirmed upar).
    _deadline = _t.monotonic() + 40.0
    while True:
        try:
            if _hx.get(settings.shopapp_health_url, timeout=4.0).status_code == 200:
                break
        except Exception:  # noqa: BLE001 - abhi down hai, aur intezar
            pass
        if _t.monotonic() > _deadline:
            pytest.fail("stable-broken baseline nahi bana — shopapp 40s mein wapas 200 nahi de paya")
        _t.sleep(1.0)

    yield fault
    fault.heal_fn()  # chahe test fail ho, world reset hona chahiye


def test_naive_says_true_on_f4_restart(f4_broken_with_wrong_fix):
    """Control group: akela idle probe PASS bolta hai — yahi to bug hai."""
    from oncall.oracle.health import naive_is_recovered

    ok, probe = asyncio.run(naive_is_recovered(settings.shopapp_health_url))
    assert ok, (
        f"naive ne PASS NAHI bola ({probe.status}/{probe.error}) — matlab ya "
        "fault inject nahi hua, ya POOL_MIN badal gaya hai. Dono N1 todte hain."
    )


def test_sustained_says_false_on_f4_restart(f4_broken_with_wrong_fix):
    """🔥 THE test. Sustained FAIL bolega — warna project ki thesis toot gayi."""
    from oncall.oracle.health import is_recovered

    verdict = asyncio.run(
        is_recovered(settings.shopapp_health_url, f"{settings.shopapp_url}/orders")
    )
    assert verdict.load is not None and verdict.load.fired > 0, (
        "load generator ne ek bhi request nahi maari — bina load ke F4 kabhi "
        "pakda nahi jaata (oncall/oracle/load.py ka BURST section dekho)"
    )
    assert not verdict.sustained_recovered, (
        "🔥 ORACLE BROKEN: sustained ne F4+restart ko PASS bol diya. Ye D1 ka "
        f"falsifier hai. Probes: {[p.to_payload() for p in verdict.probes]}"
    )
