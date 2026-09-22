"""LLM router — poore project ke SIRF do call sites yahan se guzarte hain.

────────────────────────────────────────────────────────────────────────────
🔴 Ye file kyun exist karti hai (ek line mein)
────────────────────────────────────────────────────────────────────────────
"Kitne tokens kharch hue?" aur "kaunsa provider down tha?" — dono ke jawab
`llm_calls` table mein hain, aur wo table sirf YE file bharta hai. Agar
diagnose/propose apne-aap HTTP maarte, toh har naya call site ek naya
token-accounting bug hota. Router = ek hi jagah jahan latency, usage, aur
failures record hote hain — fail hui call BHI (`ok=false`), kyunki R8 ka
confound-check aur TOKEN_BUDGET ka spend-track dono ko failed calls ka data
chahiye.

────────────────────────────────────────────────────────────────────────────
Fallback chain vs PIN (R8 — ye project ka measurement-safety rule hai)
────────────────────────────────────────────────────────────────────────────
Default chain: Groq → Gemini → OpenRouter. Lekin `ONCALL_PIN_PROVIDER` set ho
(default: `groq`) toh **sirf wahi provider**, aur uska fail hona matlab run
FAIL — fallback NAHI.

Kyun: ablation ke do arms ek hi provider pe chalne chahiye. Aadha run Groq pe
aur aadha Gemini pe gaya toh N3 delta me model ka farq bhi ghul-mil jaata — wo
ablation nahi, do confounded variables hain. Isliye pin mode mein fallback
jaan-boojh kar band hai, aur failure loudly aata hai (`TOKEN_BUDGET.md` §8).

Pin khaali string (`ONCALL_PIN_PROVIDER=`) karne pe chain active hoti hai —
daily dev ke liye theek, measurement runs ke liye KABHI nahi.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from oncall.config import load_settings
from oncall.db.queries import record_llm_call


class LLMUnavailableError(RuntimeError):
    """Saare providers try ho gaye (ya pinned provider fail hua). Caller isko
    `ESCALATED reason='llm_unavailable'` mein badalta hai — retry loop nahi,
    honest stop."""


class ParseError(ValueError):
    """Response aaya lekin valid JSON/model-shape nahi tha. Caller EK baar
    error-message ke saath retry karta hai; dobara fail = `parse_failed`."""


#: 429/timeouts pe backoff. 2s se shuru kyunki Groq ka TPM window per-minute
#: hai — 8s tak wait karne se agla minute shuru ho jaata hai.
BACKOFF_S: tuple[float, ...] = (2.0, 4.0, 8.0)

#: Output cap. Diagnosis JSON ~180 tokens, proposal ~90; 700 headroom deta hai
#: bina runaway generation ke.
MAX_COMPLETION_TOKENS = 700

#: Temperature jaan-boojh kam (0.2). Ye creative writing nahi — same logs pe
#: do runs ka jawaab jitna paas-paas ho, utna N-metrics ka confound kam.
TEMPERATURE = 0.2


@dataclass(frozen=True)
class LLMResult:
    """Ek safal call ka output + accounting. `raw_response` debugging ke liye."""

    text: str
    provider: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int


# ─────────────────────────────────────────────────────────────────────────────
# JSON extraction — LLM text ke aage-peeche ki baatein kaatne ke liye
# ─────────────────────────────────────────────────────────────────────────────
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Response text → dict. Code-fence, leading prose, trailing prose sab seh leta hai.

    🔴 Regex se JSON *fix* nahi karte — sirf FENCE nikalta hai, phir strict
       `json.loads`. Adha-tuta JSON silently repair karna R14 wali galti hai:
       corrupt data parse-ok ban jaata. Jo toota hai wo ParseError dega, aur
       caller retry-with-error ka legal raasta use karega.
    """
    candidate = text.strip()
    m = _FENCE_RE.search(candidate)
    if m:
        candidate = m.group(1).strip()
    else:
        # Bina fence ke: pehle `{` se aakhri `}` tak — "Sure! Here it is: {...}"
        # pattern ka sasta bachaav.
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            candidate = candidate[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ParseError(f"JSON nahi bana ({exc}): {text[:200]!r}") from None
    if not isinstance(parsed, dict):
        raise ParseError(f"JSON object nahi tha: {type(parsed).__name__}")
    return parsed


# ─────────────────────────────────────────────────────────────────────────────
# Provider adapters — teeno OpenAI-compatible chat shape lautte hain
# ─────────────────────────────────────────────────────────────────────────────


def _providers_in_order(pin: str) -> list[str]:
    order = ["groq", "gemini", "openrouter"]
    if pin:
        return [pin]
    return order


def _call_groq(model: str, messages: list[dict[str, str]]) -> tuple[str, int | None, int | None]:
    """Groq SDK. 🔴 Import function ke ANDAR hai — groq package optional rakhta
    hai ye file ko, taaki router ka unit-test bina network/SDK bhi chale."""
    from groq import Groq  # noqa: PLC0415 - deliberate lazy import

    client = Groq()  # api key env se khud padhta hai (GROQ_API_KEY)
    resp = client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        temperature=TEMPERATURE,
        max_tokens=MAX_COMPLETION_TOKENS,
        response_format={"type": "json_object"},
    )
    usage = getattr(resp, "usage", None)
    return (
        resp.choices[0].message.content or "",
        getattr(usage, "prompt_tokens", None),
        getattr(usage, "completion_tokens", None),
    )


def _call_openai_compatible(
    name: str, base_url: str, api_key: str, model: str, messages: list[dict[str, str]]
) -> tuple[str, int | None, int | None]:
    """Gemini/OpenRouter ka shared path — dono OpenAI-compatible endpoints hain,
    toh ek hi code path rehta hai (retry/timeout/usage parsing ek jagah)."""
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": messages,
                "temperature": TEMPERATURE,
                "max_tokens": MAX_COMPLETION_TOKENS,
                "response_format": {"type": "json_object"},
            },
        )
        if resp.status_code == 429:
            raise _RateLimited(f"{name}: 429")
        resp.raise_for_status()
        data = resp.json()
    content = data["choices"][0]["message"]["content"] or ""
    usage = data.get("usage") or {}
    return (
        content,
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
    )


class _RateLimited(Exception):
    """429 mila — backoff lagao ya agla provider dekho. Internal signal."""


def _dispatch(provider: str, model: str, messages: list[dict[str, str]]) -> tuple[str, int | None, int | None]:
    if provider == "groq":
        return _call_groq(model, messages)
    if provider == "gemini":
        s = load_settings()
        return _call_openai_compatible(
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            s.gemini_api_key,
            s.gemini_model,
            messages,
        )
    if provider == "openrouter":
        s = load_settings()
        return _call_openai_compatible(
            "openrouter",
            "https://openrouter.ai/api/v1",
            s.openrouter_api_key,
            s.openrouter_model,
            messages,
        )
    raise LLMUnavailableError(f"unknown provider '{provider}'")


def _provider_key_ok(provider: str) -> bool:
    s = load_settings()
    if provider == "groq":
        return bool(s.groq_api_key)
    if provider == "gemini":
        return bool(s.gemini_api_key)
    if provider == "openrouter":
        return bool(s.openrouter_api_key)
    return False


# ═════════════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════════════
def call(
    *,
    incident_id: int,
    attempt: int,
    call_site: str,
    system_prompt: str,
    user_prompt: str,
) -> LLMResult:
    """Ek LLM call. Har try — pass ya fail — `llm_calls` mein likha jaata hai.

    Args:
        call_site: 'diagnose' ya 'propose'. DB CHECK inhi do ko allow karta hai
            — teesra call site add karna DB-level rukta hai, jo "exactly 2
            call sites" claim ka enforcement hai (TDD §5).

    Raises:
        LLMUnavailableError: pinned provider fail / saari chain khatam.
    """
    settings = load_settings()
    models = {"groq": settings.groq_model}
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_error = ""
    for provider in _providers_in_order(settings.pin_provider):
        if not _provider_key_ok(provider):
            last_error = f"{provider}: API key is not set"
            continue
        model = models.get(provider, "")
        for wait_s in (None, *BACKOFF_S):  # pehla try free, phir backoff rounds
            started = time.monotonic()
            try:
                text, ptoks, ctoks = _dispatch(provider, model, messages)
            except _RateLimited as exc:
                last_error = str(exc)
                if wait_s is not None:
                    time.sleep(wait_s)
                continue
            except Exception as exc:  # noqa: BLE001 - provider errors sab "down" hain
                last_error = f"{provider}: {type(exc).__name__}: {str(exc)[:200]}"
                _record(incident_id, attempt, call_site, provider, model,
                        ok=False, error=last_error, latency_ms=_ms(started))
                break  # agla provider
            else:
                result = LLMResult(
                    text=text,
                    provider=provider,
                    model=model,
                    prompt_tokens=ptoks,
                    completion_tokens=ctoks,
                    latency_ms=_ms(started),
                )
                _record(incident_id, attempt, call_site, provider, model,
                        ok=True, raw=text, prompt_toks=ptoks, completion_toks=ctoks,
                        latency_ms=result.latency_ms)
                return result
    raise LLMUnavailableError(
        f"koi provider nahi bola (last: {last_error}) — "
        ".env mein GROQ_API_KEY check karo"
    )


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _record(
    incident_id: int,
    attempt: int,
    call_site: str,
    provider: str,
    model: str,
    *,
    ok: bool,
    error: str | None = None,
    raw: str | None = None,
    prompt_toks: int | None = None,
    completion_toks: int | None = None,
    latency_ms: int | None = None,
) -> None:
    """Accounting write. 🔴 Fail hone pe BHI likhta hai — wahi survivorship-bias
    bachaav jo broker ke log-before-execute mein hai."""
    try:
        record_llm_call(
            incident_id=incident_id,
            call_site=call_site,
            attempt=attempt,
            provider=provider,
            model=model,
            ok=ok,
            prompt_tokens=prompt_toks,
            completion_tokens=completion_toks,
            latency_ms=latency_ms,
            raw_response=(raw or "")[:4000],
            error=(error[:500] if error else None),
        )
    except Exception:  # noqa: BLE001 - accounting fail hona diagnosis ko nahi roknA chahiye
        # Accounting secondary hai: primary kaam (diagnosis/proposal) chalta
        # rahega chahe DB write fail ho. Chup-chaap nahi — stderr pe dikhta hai.
        import sys

        print("llm_calls record FAIL (accounting only)", file=sys.stderr)
