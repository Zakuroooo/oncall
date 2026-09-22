"""Wilson score interval — chhote sample pe honest confidence interval.

~15 lines. `statsmodels` JAAN-BOOJH NAHI lagaya — 4-line formula hai, aur isko
khud likhne se main whiteboard pe derive kar sakta hoon (`METRICS.md` §5, aur
CLAUDE.md §1: 20 lines jo samajh aayein > ek library jo nahi aaye).

────────────────────────────────────────────────────────────────────────────
Problem: normal approximation chhote n pe tootti hai
────────────────────────────────────────────────────────────────────────────
`p̂ ± 1.96·√(p̂(1-p̂)/n)` — n=20, x=20 (100%) pe interval [1.0, 1.0] deta hai,
yaani "pakka 100%, zero uncertainty" — jo jhoot hai. Wilson interval extreme
values pe bhi sane rehta hai: wahi case [0.839, 1.000] deta hai.

Mera asli case (METRICS.md §5): n=20, x=14 → point estimate 70% lekin interval
[48.1%, 85.5%] — width ~37pp. 🔴 Wo cell `underpowered` label paayega, aur ye
helper wo label khud compute karta hai — taaki UI pe bare percentage kabhi
akela na dikhe.
"""

from __future__ import annotations

import math

#: 95% two-sided z. Literal isliye taaki "95%" ka daawa code se match kare.
Z = 1.959964

#: Isse chaudha interval = `underpowered` badge (`METRICS.md` §5 ki table:
#: width ≤ 25pp target tha; uske upar number bharose-layak nahi).
UNDERPOWERED_PP = 25.0


def wilson(successes: int, n: int) -> tuple[float, float]:
    """`(low, high)` proportions mein [0..1]. n=0 pe (0.0, 1.0) — poori
    uncertainty, koi crash nahi."""
    if n <= 0:
        return (0.0, 1.0)
    x = float(max(0, min(int(successes), n)))
    p = x / n
    z2 = Z * Z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = (Z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


def interval_pp(successes: int, n: int) -> tuple[float, float]:
    """Same interval, percentage points mein — UI/table ke liye."""
    lo, hi = wilson(successes, n)
    return (lo * 100.0, hi * 100.0)


def is_underpowered(successes: int, n: int) -> bool:
    """Interval ki width > 25pp? Ye badge har rate ke saath jaata hai."""
    lo, hi = interval_pp(successes, n)
    return (hi - lo) > UNDERPOWERED_PP


def describe(successes: int, n: int) -> dict[str, object]:
    """Ek row ka poora honest package: rate + CI + badge. UI isi ko dikhati hai.

    Bare percentage akela KABHI return nahi hota — wahi G3 gate (`METRICS.md`
    §1): "koi number bina interval claim nahi hoga".
    """
    if n <= 0:
        return {"n": 0, "rate_pct": None, "ci_low_pct": None, "ci_high_pct": None,
                "underpowered": True}
    lo, hi = interval_pp(successes, n)
    return {
        "n": n,
        "rate_pct": round(100.0 * successes / n, 1),
        "ci_low_pct": round(lo, 1),
        "ci_high_pct": round(hi, 1),
        "underpowered": is_underpowered(successes, n),
    }
