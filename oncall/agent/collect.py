"""Log collection — LLM #1 ka INPUT. Yahan se diagnosis ki quality decide hoti hai.

────────────────────────────────────────────────────────────────────────────
Teen filter, teen alag wajah (`LOGIC.md` §3 Step 3 ka code roop)
────────────────────────────────────────────────────────────────────────────
| Filter | Kya | Kyun |
|---|---|---|
| Level | sirf ERROR / FATAL / CRITICAL lines | 400 raw lines ~9,000 tokens hain; poora budget 8K TPM hai (`TOKEN_BUDGET.md` §2) |
| Truncate | per line 300 characters | ek stack trace ki lambi line poora prompt kha sakti hai |
| Cap | max 12 lines | bounded input = predictable cost, har baar |

🔴 Chautha filter yahan NAHI hai: koi summarization nahi. Logs ko LLM se
   summarize karwana ~2,000 extra tokens tha — plain string ops free hain
   (`TOKEN_BUDGET.md` §5). Isliye ye file mein koi model import nahi hai.

────────────────────────────────────────────────────────────────────────────
🔒 Logs UNTRUSTED INPUT hain (`THREAT_MODEL.md` §5)
────────────────────────────────────────────────────────────────────────────
Koi bhi shopapp tak request bhejne wala log likhwa sakta hai. Agar us log mein
likha ho "IGNORE PREVIOUS INSTRUCTIONS..." toh wo seedhe LLM #1 ke prompt mein
jaata. Do bachaav, dono yahin se shuru hote hain:

1. Heuristic scan — injection jaisi strings mili toh flag uth jaata hai, aur wo
   flag approval UI tak ⚠️ badge banta hai. Hum log ko sanitize NAHI karte —
   tampering evidence badal deti. Flag + original text jaata hai.
2. Prompt-side wrapping — `diagnose.py` har line ko `<untrusted_log_output>`
   tags mein lapeta hai aur system prompt mein saaf likhta hai ki data data
   hai, instruction nahi. Wo wrapping isi file ka output shape follow karti hai.
"""

from __future__ import annotations

import re

from dataclasses import dataclass

# 🔒 Container-name map ka EK maalik `handlers._CONTAINERS` hai. Doosri copy
#    banana wo drift hai jisme F5 ka localization silently toot jaata (naam ka
#    typo = galat container ke logs). Private naam import karna thoda gadbad
#    lagta hai, lekin do jagah ek hi dict likhne se zyada kamzor hai.
from oncall.actions.handlers import _CONTAINERS

#: Kitni aakhri lines pull karni hain. 400 = shopapp ke ~10 min ke traffic ke
#: barabar. Zyada = bekaar bytes, kam = error miss ho sakta hai.
TAIL_LINES = 2000  # was 400: 15 tracebacks under load are ~900 lines and pushed the root cause out (2026-10-15). Output stays capped at MAX_LINES.

#: Filtered output ki hard cap. `TOKEN_BUDGET.md` §2 ka math isi number pe
#: khada hai (12 × ~75 tokens ≈ 900).
MAX_LINES = 12

#: Ek line ka max length. Stack traces ki lambi frames yahan cut hoti hain.
MAX_LINE_CHARS = 300

#: Ye teen token hi level-filter hain. Uppercase match jaan-boojh kar: log
#: levels conventionally uppercase hote hain, aur lowercase "error" prose
#: messages mein bahut aam hai ("0 errors") — wo noise hoti, signal nahi.
_LEVEL_TOKENS = ("ERROR", "FATAL", "CRITICAL")
_LEVEL_RE = re.compile(r"\b(?:" + "|".join(_LEVEL_TOKENS) + r")\b")
#: `SomeError: message` / `pkg.SomeException: message` — the last line of a traceback.
_EXC_RE = re.compile(r"^(?:[a-z_]\w*\.)+[A-Z]\w*: |^[A-Z][a-z]\w*(?:Error|Exception): ")

#: Injection heuristics — chhoti jaan-boojh kar. Poora nahi ho sakta; ye
#: tripwire hai, firewall nahi. Asli bachaav allowlist hai (D6).
_INJECTION_PATTERNS = (
    "ignore previous",
    "disregard previous",
    "ignore all previous",
    "you are now",
    "new instructions:",
    "system:",
    "</untrusted_log_output>",  # apne hi tag ko todne ki koshish
)


@dataclass(frozen=True)
class CollectedLogs:
    """Ek collection round ka output. `events` (kind='logs') ka payload yahi
    banta hai, aur `diagnose.py` ka user prompt bhi."""

    service: str
    container_state: str  #: running / exited / paused — Docker ki State.Status
    exit_code: int | None  #: exited hone pe kaunsa code (F1 → 137)
    lines: tuple[str, ...]
    raw_line_count: int
    injection_suspected: bool

    def to_payload(self) -> dict:
        """`record_event(kind='logs', payload=...)` ke liye."""
        return {
            "service": self.service,
            "container_state": self.container_state,
            "exit_code": self.exit_code,
            "raw_line_count": self.raw_line_count,
            "kept_line_count": len(self.lines),
            "lines": list(self.lines),
            "injection_suspected": self.injection_suspected,
        }


def filter_lines(raw: str) -> tuple[tuple[str, ...], int]:
    """Raw log text → (kept lines, total lines read). Pure function — testable.

    Kept: lines with an uppercase ERROR / FATAL / CRITICAL *word* anywhere, and
    Python exception summary lines (`psycopg.OperationalError: ...`).
    Dropped: traceback frames (`File "..."`) and exact repeats.
    Returned: the MOST RECENT `MAX_LINES` matches, oldest first.

    🔴 Changed 2026-10-15 after F4 incidents #10/#11. The old rule ("token in the
       first 80 chars, keep the first 12") failed twice over: every traceback
       frame matched because its path contains `errors.py`, and the root cause
       `... WARNING psycopg.pool: ... FATAL: sorry, too many clients already`
       has FATAL at char ~170, so it was never kept. The model then saw twelve
       stack frames and diagnosed "unhandled exceptions in the ASGI app".
    """
    matches: list[str] = []
    total = 0
    for raw_line in raw.splitlines():
        line = raw_line.strip().replace("\r", "")
        if not line:
            continue
        total += 1
        if line.startswith('File "'):
            continue
        if not (_LEVEL_RE.search(line) or _EXC_RE.match(line)):
            continue
        if len(line) > MAX_LINE_CHARS:
            # Cut hone ka pata hona zaruri hai — warna reader sochta hai poori
            # line yahi thi.
            line = line[: MAX_LINE_CHARS - 3] + "..."
        if line in matches:
            matches.remove(line)  # keep only the latest occurrence
        matches.append(line)
    return tuple(matches[-MAX_LINES:]), total


def scan_injection(lines: tuple[str, ...]) -> bool:
    """Kya log mein prompt-injection jaisi kuch dikhi? `True` = shak, saboot nahi."""
    joined = "\n".join(lines).lower()
    return any(pat in joined for pat in _INJECTION_PATTERNS)


def gather(service: str = "shopapp") -> CollectedLogs:
    """Container ka status + filtered error lines. Kabhi exception nahi phenkta.

    🔴 Container GAYAB ho (F1 ke baad restart se pehle, ya compose down) toh
       `NotFound` ek normal observation hai — "container nahi hai" khud ek
       diagnosis hai. Isliye ye function exception ko `CollectedLogs` mein
       badalta hai, upar nahi phenkta. Wahi pattern `oracle/probe_once()` ka hai:
       failure bhi measurement hai.

    Returns:
        `CollectedLogs` — khaali lines ke saath bhi usable.
    """
    import docker
    from docker.errors import NotFound

    name = _CONTAINERS[service]

    try:
        container = docker.from_env().containers.get(name)
    except NotFound:
        return CollectedLogs(
            service=service,
            container_state="not_found",
            exit_code=None,
            lines=(f"container '{name}' does not exist (compose down? F1?)",),
            raw_line_count=0,
            injection_suspected=False,
        )

    container.reload()
    state = container.attrs.get("State", {})
    raw = container.logs(tail=TAIL_LINES)
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    lines, total = filter_lines(text)

    return CollectedLogs(
        service=service,
        container_state=str(state.get("Status", "unknown")),
        exit_code=state.get("ExitCode"),
        lines=lines,
        raw_line_count=total,
        injection_suspected=scan_injection(lines),
    )
