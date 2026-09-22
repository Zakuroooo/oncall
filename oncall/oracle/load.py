"""Load generator — oracle ka wo hissa jo F4 ko chhupne nahi deta.

─────────────────────────────────────────────────────────────────────────────
🔴 YE FILE KYUN EXIST KARTI HAI (ek line mein)
─────────────────────────────────────────────────────────────────────────────
F4 fault (`max_connections = 5`) ek IDLE service pe bilkul theek dikhta hai.
Kyun: shopapp ka connection pool `POOL_MIN = 1` pe khada hai, toh idle mein use
ek hi connection chahiye — aur ek connection 5 ki limit mein aaram se
fit ho jaata hai. `/health` 200 deta hai. Sab hara dikhta hai.

Fault SIRF load ke neeche dikhta hai: jab concurrent requests aati hain, pool
`POOL_MAX = 10` ki taraf badhta hai, 6th connection maangta hai, aur Postgres
bolta hai `FATAL: sorry, too many clients already`.

Matlab: **is file ke bina oracle jhooth bolega.** Load probe ka optional
add-on nahi hai — wo probe ka pre-condition hai.

─────────────────────────────────────────────────────────────────────────────
🔥 BURST vs EVENLY-SPACED — ye ek load-bearing design detail hai
─────────────────────────────────────────────────────────────────────────────
Neeche `generate_load()` har tick pe saari `rps` requests **ek saath** fire
karta hai (`for _ in range(rps): create_task(...)`), phir second ka bacha hua
hissa sota hai. Ye jaan-boojh kar hai.

Agar hum 20 requests ko poore second mein barabar phaila dete (har 50 ms pe ek),
toh ek ~5 ms query pe in-flight concurrency ≈ 20 × 0.005 = **0.1 connections**
hoti. Pool 1–2 connection se aage badhta hi nahi, `max_connections = 5` ki
deewar kabhi nahi aati, aur **F4 load ke neeche bhi invisible reh jaata.**
Yaani naive-vs-sustained disagreement — poore project ka headline result —
gayab ho jaata, aur ye galti chup-chaap hoti (koi error nahi, bas "sab pass").

Isliye burst. Ek burst 20 simultaneous requests deta hai → pool ko 10 tak
badhna padta hai → 6th connection pe FATAL. Yahi wo signal hai jo chahiye.

─────────────────────────────────────────────────────────────────────────────
🔴 "20 rps" ka matlab yahan kya hai
─────────────────────────────────────────────────────────────────────────────
Har second mein 20 requests FIRE hoti hain — 20 requests COMPLETE nahi hoti.
Farq zaruri hai: agar hum "20 complete" ka target rakhte, toh service dheemi
hone pe generator khud dheema ho jaata aur load kam ho jaata — bilkul us waqt
jab load sabse zyada chahiye tha. Ise closed-loop vs open-loop load kehte hain,
aur load testing ki sabse aam galti closed-loop use karna hai
(`docs/LEARNINGS.md` — "coordinated omission").

Isliye: fixed rate, fire-and-forget-ish. Requests fail hongi — wo data hai,
problem nahi.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import httpx


@dataclass
class LoadResult:
    """Ek load window ka nateeja. Ye evidence hai, verdict nahi.

    🔴 Oracle ka verdict `health.py` deta hai. Ye class sirf ginti karti hai.
       Wajah CLAUDE.md §3: verdict ka ek hi writer hona chahiye.
    """

    fired: int = 0  #: kitni requests bheji gayi
    ok: int = 0  #: kitni ne 2xx diya
    failed: int = 0  #: kitni ne non-2xx diya ya exception phenka
    status_counts: dict[int, int] = field(default_factory=dict)
    errors: dict[str, int] = field(default_factory=dict)
    duration_s: float = 0.0

    @property
    def error_rate(self) -> float:
        """0.0 se 1.0. `fired == 0` pe 0.0 — division by zero se bachne ke liye,
        aur kyunki "koi request nahi bheji" ka matlab "koi error nahi" hai."""
        if self.fired == 0:
            return 0.0
        return self.failed / self.fired

    def to_payload(self) -> dict:
        """`events.payload` (jsonb) mein jaane layak dict.

        🔴 `status_counts` ki keys `int` hain, aur JSON keys hamesha string
           hoti hain. Isliye yahan explicitly `str(k)` — warna psycopg ka
           json serializer chalta hai lekin wapas padhne pe keys string ban
           jaati hain aur code `counts[200]` pe KeyError deta hai. Ek bug jo
           write pe nahi, read pe dikhta hai.
        """
        return {
            "fired": self.fired,
            "ok": self.ok,
            "failed": self.failed,
            "error_rate": round(self.error_rate, 4),
            "status_counts": {str(k): v for k, v in sorted(self.status_counts.items())},
            "errors": dict(sorted(self.errors.items())),
            "duration_s": round(self.duration_s, 3),
        }


async def _one_request(client: httpx.AsyncClient, url: str, result: LoadResult) -> None:
    """Ek request. Kabhi exception nahi phenkta — sab kuch `result` mein ginta hai.

    🔴 Ye function kyun kuch raise nahi karta: ye `asyncio.gather()` mein 100+
       baar chalta hai. Ek raise poore gather ko gira deta (ya
       `return_exceptions=True` ke saath ek list of exceptions deta jise phir
       parse karna padta). Load generator ka kaam load daalna hai, error handling
       ki nayi problem paida karna nahi.
    """
    result.fired += 1
    try:
        resp = await client.get(url)
    except Exception as exc:
        # 🔴 Bare `Exception` yahan sahi hai: httpx timeout, connection refused,
        #    DNS failure, read error — ye SAB "service load ke neeche kaam nahi
        #    kar rahi" ka same matlab rakhte hain. Inko alag-alag pakadne se
        #    verdict nahi badalta, sirf code lamba hota hai.
        #    Lekin exception ka NAAM rakha jaata hai — wo LLM #1 ka evidence hai.
        name = type(exc).__name__
        result.errors[name] = result.errors.get(name, 0) + 1
        result.failed += 1
        return

    code = resp.status_code
    result.status_counts[code] = result.status_counts.get(code, 0) + 1
    if 200 <= code < 300:
        result.ok += 1
    else:
        result.failed += 1


async def generate_load(
    url: str,
    *,
    rps: int,
    seconds: float,
    timeout_s: float = 5.0,
) -> LoadResult:
    """`url` pe `seconds` ke liye `rps` requests/second daalo.

    Args:
        url: kaunsa endpoint. 🔴 `/orders` daalna chahiye, `/health` nahi —
            `/health` ek hi query karta hai, `/orders` pool se connection
            maangta hai. Load ka maqsad pool ko badhaana hai.
        rps: har second kitni requests FIRE karni hain (open-loop).
        seconds: kitni der.
        timeout_s: ek request kitna wait karegi.

    Returns:
        LoadResult — ginti, status codes, error names.

    ─────────────────────────────────────────────────────────────────────────
    🔴 `finally` block ka poora point
    ─────────────────────────────────────────────────────────────────────────
    Agar oracle ke beech mein koi exception aata hai (ya user Ctrl-C karta hai),
    ye function ke andar chal rahi ~200 pending requests kahin nahi jaati — wo
    background mein service ko peet-ti rehti hain. Agla probe us bache hue load
    ke saath chalta hai aur uska nateeja galat aata.

    `async with httpx.AsyncClient(...)` connections band karta hai, aur
    `finally` mein `gather` ka cancel pending tasks ko maarta hai. Ek load
    generator jo saaf-saaf nahi rukta, wo agle measurement ko contaminate
    karta hai.
    """
    result = LoadResult()
    tasks: list[asyncio.Task] = []
    started = time.monotonic()

    # `limits` kyun: default httpx pool 100 connections rakhta hai. 20 rps × 5s
    # = 100 concurrent tasks ho sakti hain agar service dheemi ho. Isko explicit
    # rakha hai taaki generator khud bottleneck na bane.
    limits = httpx.Limits(max_connections=rps * 3, max_keepalive_connections=rps)

    async with httpx.AsyncClient(timeout=timeout_s, limits=limits) as client:
        try:
            # Ek-ek tick pe `rps` requests. `sleep` se rate control hota hai.
            total_ticks = max(1, int(seconds))
            for tick in range(total_ticks):
                tick_start = time.monotonic()
                for _ in range(rps):
                    tasks.append(asyncio.create_task(_one_request(client, url, result)))

                # Agla tick theek 1 second baad. Jo waqt requests banane mein
                # laga, use ghata diya — warna rate dheere-dheere gir jaata.
                elapsed = time.monotonic() - tick_start
                sleep_for = max(0.0, 1.0 - elapsed)
                # Aakhri tick ke baad sona bekaar hai.
                if tick < total_ticks - 1:
                    await asyncio.sleep(sleep_for)

            # Sab pending requests ko poora hone do. `return_exceptions=True`
            # defensive hai — `_one_request` khud kuch raise nahi karta.
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            # 🔴 Koi bhi task bacha ho toh cancel. Ye leak-prevention hai.
            for t in tasks:
                if not t.done():
                    t.cancel()
            result.duration_s = time.monotonic() - started

    return result
