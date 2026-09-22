"""🔥 Health oracle — is project ka dil. Verdict ka EKMATRA writer.

─────────────────────────────────────────────────────────────────────────────
🔴 CLAUDE.md §0 ki do line yahan code ban jaati hain
─────────────────────────────────────────────────────────────────────────────
    "A model's opinion is never evidence. `oncall/oracle/health.py` is the only
     writer of verdicts."

Iska matlab practical terms mein: is file mein koi LLM call NAHI hai, koi
provider import NAHI hai, koi prompt NAHI hai. Ye file sirf HTTP maarti hai aur
ginti karti hai. Agar kal koi is file mein "model se pooch lo" jaisa kuch add
kare, poore project ka claim khatam ho jaata hai.

─────────────────────────────────────────────────────────────────────────────
🔴 Do function, jaan-boojh kar
─────────────────────────────────────────────────────────────────────────────
`naive_is_recovered()`  — ek probe, koi load nahi. Ye BUG NAHI HAI.
`is_recovered()`        — 3 probes / 30 s / ~20 rps.

`naive_*` ka poora maqsad ek **control group** hai. Science mein claim ye hai:
"sustained checking naive checking se behtar hai." Us claim ko saabit karne ke
liye naive ka nateeja bhi record karna padta hai — warna comparison kis se hoga?

Isliye `is_recovered()` dono nikaalta hai, ek hi run mein, ek hi incident pe.
Yahi `verdicts` table ke do columns hain (`naive_recovered`,
`sustained_recovered`), aur yahi N1 metric hai.

─────────────────────────────────────────────────────────────────────────────
🔴 Order zaruri hai: naive PEHLE, load BAAD mein
─────────────────────────────────────────────────────────────────────────────
Naive probe load se PEHLE chalti hai. Kyun: naive implementation ka poora
character ye hai ki wo fix ke turant baad, idle service pe check karti hai.
Agar hum load daal kar naive probe lete, toh wo naive nahi rehti — wo ek
sustained probe ban jaati bas kam probes ke saath, aur N1 ka comparison
bekaar ho jaata.

Sequence: fix hua → naive probe (t≈0, idle) → load ON → probe 1 (t=10) →
probe 2 (t=20) → probe 3 (t=30) → load OFF → verdict.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import httpx

from oncall.config import (
    ORACLE_LOAD_RPS,
    ORACLE_PROBE_GAP_S,
    ORACLE_PROBE_TIMEOUT_S,
    ORACLE_PROBES,
)
from oncall.oracle.load import LoadResult, generate_load


@dataclass(frozen=True)
class Probe:
    """Ek probe ka raw record. `frozen=True` — ek liya gaya measurement badalta nahi."""

    n: int  #: kaunsi probe (1-indexed) — timeline padhne ke liye
    ok: bool  #: 2xx mila?
    ms: int  #: kitna time laga
    status: int | None = None  #: HTTP code, ya None agar request hi fail hui
    error: str | None = None  #: exception ka NAAM — LLM #1 ka evidence
    at_s: float = 0.0  #: oracle start se kitne second baad

    def to_payload(self) -> dict:
        """`verdicts.probes` (jsonb) mein jaane layak."""
        return {
            "n": self.n,
            "ok": self.ok,
            "ms": self.ms,
            "status": self.status,
            "error": self.error,
            "at_s": round(self.at_s, 1),
        }


@dataclass(frozen=True)
class Verdict:
    """🔥 Oracle ka output. `verdicts` table ka ek row.

    🔴 Ye `frozen=True` hai jaan-boojh kar. Ek verdict banne ke baad koi use
       "adjust" nahi kar sakta. Agar retry karna hai toh naya attempt, naya
       verdict — purana record rehta hai. Audit trail overwrite nahi hoti.
    """

    sustained_recovered: bool  #: 🔥 ASLI verdict
    naive_recovered: bool  #: control group ka verdict
    probes: list[Probe]
    load: LoadResult | None
    load_rps: int
    window_s: int
    elapsed_s: float

    @property
    def disagreed(self) -> bool:
        """🔥 N1 ka dil. `True` matlab naive ne "fixed" bola aur sustained ne nahi.

        Yahi wo ek boolean hai jiske liye poora project bana hai. F4 pe ye
        `True` aana chahiye. ⚠️ Abhi tak measure nahi hua (Phase 8).
        """
        return self.naive_recovered and not self.sustained_recovered

    def to_row(self) -> dict:
        """`db.queries.insert_verdict()` ke liye ready dict."""
        return {
            "sustained_recovered": self.sustained_recovered,
            "naive_recovered": self.naive_recovered,
            "probes": [p.to_payload() for p in self.probes],
            "load_rps": self.load_rps,
            "window_s": self.window_s,
        }


# ═════════════════════════════════════════════════════════════════════════════
# 1. Ek probe
# ═════════════════════════════════════════════════════════════════════════════


async def probe_once(
    client: httpx.AsyncClient,
    url: str,
    *,
    n: int = 1,
    at_s: float = 0.0,
) -> Probe:
    """Ek HTTP GET. Kabhi raise nahi karta — failure bhi ek measurement hai.

    🔴 Ye function exception ko `Probe.error` mein badal deta hai, phenkta nahi.
       Wajah: oracle ka kaam "pata karna" hai, "shikayat karna" nahi. Agar ye
       raise karta, toh `is_recovered()` ke andar har call ko try/except mein
       lapetna padta — aur ek bhoola hua except poore oracle ko gira deta us
       waqt jab service down thi. Yaani oracle theek us haalat mein crash karta
       jise measure karne ke liye wo bana hai.

    🔴 `type(exc).__name__` preserve karna zaruri hai. `ConnectTimeout` aur
       `ReadTimeout` do bilkul alag kahaniyan hain: pehla "service ne TCP
       accept nahi kiya" (container down), doosra "connect ho gaya lekin jawab
       nahi aaya" (DB block, F3). LLM #1 ko yahi farq chahiye.
    """
    started = time.monotonic()
    try:
        resp = await client.get(url)
    except Exception as exc:
        ms = int((time.monotonic() - started) * 1000)
        return Probe(
            n=n,
            ok=False,
            ms=ms,
            status=None,
            error=type(exc).__name__,
            at_s=at_s,
        )

    ms = int((time.monotonic() - started) * 1000)
    return Probe(
        n=n,
        ok=200 <= resp.status_code < 300,
        ms=ms,
        status=resp.status_code,
        error=None,
        at_s=at_s,
    )


# ═════════════════════════════════════════════════════════════════════════════
# 2. naive_is_recovered — 🔴 CONTROL GROUP, bug nahi
# ═════════════════════════════════════════════════════════════════════════════


async def naive_is_recovered(
    health_url: str,
    *,
    timeout_s: float = ORACLE_PROBE_TIMEOUT_S,
) -> tuple[bool, Probe]:
    """Ek probe, koi load nahi. Wahi jo 90% tutorials karte hain.

    🔴 YE JAAN-BOOJH KAR "GHATIYA" HAI. Isko theek karna project ko tod dega.

       Har self-healing demo jo internet pe hai, yahi karta hai: action chalao,
       `/health` maaro, 200 mila toh ✅ bol do. Ye function wo baseline hai jiske
       khilaaf humein measure karna hai. Agar ye behtar hota, toh `is_recovered()`
       ke exist karne ki koi wajah nahi rehti.

    Returns:
        (verdict, probe) — probe bhi wapas aata hai kyunki wo evidence hai,
        aur `verdicts.probes` mein pehli entry banta hai.
    """
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        probe = await probe_once(client, health_url, n=0, at_s=0.0)
    return probe.ok, probe


# ═════════════════════════════════════════════════════════════════════════════
# 3. 🔥 is_recovered — ASLI oracle
# ═════════════════════════════════════════════════════════════════════════════


async def is_recovered(
    health_url: str,
    load_url: str,
    *,
    probes: int = ORACLE_PROBES,
    gap_s: int = ORACLE_PROBE_GAP_S,
    rps: int = ORACLE_LOAD_RPS,
    timeout_s: float = ORACLE_PROBE_TIMEOUT_S,
) -> Verdict:
    """🔥 Verdict. 3 probes, 30 second, ~20 rps load ke neeche.

    Args:
        health_url: probe kahan maarni hai (`/health`).
        load_url: load kahan daalna hai (`/orders` — kyunki wo pool se
            connection maangta hai; `/health` ek hi query karta hai).
        probes: kitni probes (default 3).
        gap_s: probes ke beech kitne second (default 10) → window = 3 × 10 = 30 s.
        rps: har second kitni requests.

    ─────────────────────────────────────────────────────────────────────────
    🔴 SAB probes pass honi chahiye — koi "2 out of 3" nahi
    ─────────────────────────────────────────────────────────────────────────
    Ek `all()`, koi threshold nahi. Wajah: F4 ek intermittent failure hai —
    pool jab badhta hai tab failure aata hai, har request pe nahi. Agar hum
    "66% probes pass = recovered" rakhte, toh F4 kabhi-kabhi pass ho jaata aur
    result flaky ho jaata. Ek oracle jo kabhi-kabhi galat hota hai, wo oracle
    nahi hai.

    ⚠️ Iski keemat: ek asli transient network blip poore verdict ko fail kar
    dega. Us haalat mein retry (max 3) usko bacha leta hai — aur retry ka
    record `verdicts` mein alag attempt ban jaata hai, chhupta nahi.
    """
    oracle_started = time.monotonic()

    # ── Step 1: naive verdict PEHLE, load se pehle (docstring ka "Order" note)
    naive_ok, naive_probe = await naive_is_recovered(health_url, timeout_s=timeout_s)

    collected: list[Probe] = [naive_probe]
    window_s = probes * gap_s

    # ── Step 2: load ON, aur uske saath-saath probes
    #
    # 🔴 `asyncio.gather` se dono ek saath chalte hain. Sequential karna —
    #    "load daalo, phir probe karo" — galat hota: load band hone ke baad
    #    pool sikud jaata hai aur F4 phir chhup jaata. Probe ko load ke DAURAAN
    #    chalna chahiye, baad mein nahi.
    load_result: LoadResult | None = None

    async def _probe_loop() -> list[Probe]:
        out: list[Probe] = []
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            for i in range(1, probes + 1):
                # Probe se pehle gap. Pehli probe bhi t=gap pe hoti hai, t=0 pe
                # nahi — kyunki t=0 pe load abhi shuru hi hua hai aur pool ne
                # abhi badhna shuru nahi kiya.
                await asyncio.sleep(gap_s)
                at = time.monotonic() - oracle_started
                out.append(await probe_once(client, health_url, n=i, at_s=at))
        return out

    load_task = asyncio.create_task(
        generate_load(load_url, rps=rps, seconds=window_s, timeout_s=timeout_s)
    )
    probe_task = asyncio.create_task(_probe_loop())

    try:
        sustained_probes, load_result = await asyncio.gather(probe_task, load_task)
    finally:
        # 🔴 Dono task cancel karo agar koi bacha. Ek chalta hua load generator
        #    agle incident ka measurement kharab kar dega.
        for t in (load_task, probe_task):
            if not t.done():
                t.cancel()

    collected.extend(sustained_probes)

    # ── Step 3: verdict. Ek `all()`, koi threshold nahi.
    sustained_ok = len(sustained_probes) == probes and all(p.ok for p in sustained_probes)

    return Verdict(
        sustained_recovered=sustained_ok,
        naive_recovered=naive_ok,
        probes=collected,
        load=load_result,
        load_rps=rps,
        window_s=window_s,
        elapsed_s=time.monotonic() - oracle_started,
    )



