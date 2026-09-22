"""Propose — 🤖 LLM call site 2. Diagnosis + runbooks → EK allowlisted action.

────────────────────────────────────────────────────────────────────────────
🔴 "Menu" design — LLM ko CODE nahi, CHOICE milti hai
────────────────────────────────────────────────────────────────────────────
System prompt mein poora 9-action allowlist ka schema jaata hai — naam, allowed
params, allowed values. Wo text `ACTIONS` dict se BUILD hota hai (`_menu_text`),
likha hua nahi. Iska matlab: allowlist badli → prompt khud-ba-khud update.
Do jagah likha hua menu ek din allowlist se alag hota — aur wahi din LLM ko
aisa action milta jo exist hi nahi karta.

Output `llm/schema.py` ka `ProposedAction` hai: `extra="forbid"` + `Literal`
action names. Tenth action Pydantic pe mar jaata hai — broker tak pahunchta
bhi nahi.

────────────────────────────────────────────────────────────────────────────
🔴 validate_shape yahan chalti hai — execute se PEHLE nahi, APPROVAL SE PEHLE
────────────────────────────────────────────────────────────────────────────
Broker ki layers 1–3 ko approval row ki zarurat nahi (sirf shape dekhti hain).
Toh proposal DB mein likhne se PEHLE unhe chala lena free security hai: ek
invalid proposal approval queue mein pahunchti, insaan use padhta, approve
karta — aur tab jaakar broker use reject karta. Insaan ka waqt barbaad.
Isliye sequence: parse → validate_shape → (fail toh retry with error →
phir bhi fail toh parse_failed).

Execute-time par phir bhi POORI 5 layers dobara chalengi — TOCTOU (broker.py
ka header). Yahan ki check uski replacement NAHI hai.
"""

from __future__ import annotations

from typing import Any

from oncall.actions.allowlist import (
    ACTIONS,
    ALLOWED_DB_KEYS,
    ALLOWED_PATHS,
    ALLOWED_SERVICES,
    ALLOWED_TABLES,
)
from oncall.actions.broker import RejectedError, validate_shape
from oncall.db.queries import record_event
from oncall.llm import router
from oncall.llm.schema import ProposedAction


def _param_hint(param: str) -> str:
    """Ek param ke legal values ka human-readable hint — prompt ke liye."""
    if param == "service":
        return "|".join(sorted(ALLOWED_SERVICES))
    if param == "path":
        return "|".join(sorted(ALLOWED_PATHS))
    if param == "key":
        parts = [f"{key}({lo}-{hi})" for key, (lo, hi) in sorted(ALLOWED_DB_KEYS.items())]
        return "|".join(parts)
    if param == "table":
        return "|".join(sorted(ALLOWED_TABLES))
    if param == "value":
        return "int (range depends on key)"
    if param == "reason":
        return "string"
    return "?"


def build_menu_text() -> str:
    """Allowlist → prompt text. 🔴 `ACTIONS` se DERIVED hai — hand-written nahi."""
    lines = []
    for name, spec in ACTIONS.items():
        params = ", ".join(f"{p}: {_param_hint(p)}" for p in spec.params) or "(no params)"
        lines.append(f"- {name}: params = {{{params}}}")
    return "\n".join(lines)


_SYSTEM_PROMPT_TEMPLATE = """You are an SRE remediation planner.
You must choose EXACTLY ONE action from the allowlist below and output ONLY a JSON object:

{{
  "action": "<one action name from the allowlist>",
  "params": {{ ... }},
  "reasoning": "<10-500 chars: why this action, referencing the diagnosis and runbook>",
  "runbook_id": "<id of the runbook that led you here, or null>"
}}

Allowed actions:
{menu}

Rules:
- Never invent an action or a parameter that is not listed.
- If no allowed action can plausibly fix the problem, choose "escalate"
  with a clear reason. Honest refusal is a valid outcome.
- If a previous attempt already failed (see history), do NOT repeat it
  unchanged. Explain what you learned from the new evidence.
- Output JSON only. No prose before or after."""


def _history_block(history: list[dict[str, Any]]) -> str:
    """Pichhle attempts ka saansaf — D19 ka user-visible roop.

    Har entry: attempt n, action+params jo chali, verdict kya nikla. Ye block
    model ko batata hai ki duniya BADAL chuki hai — same fix dobara maangna
    mana hai."""
    if not history:
        return "(no previous attempts)"
    lines = []
    for h in history:
        outcome = "RECOVERED" if h.get("sustained") else "still failing"
        lines.append(
            f"- attempt {h.get('attempt', '?')}: "
            f"{h.get('action')}({h.get('params')}) -> {outcome}"
        )
    return "\n".join(lines)


def build_user_prompt(
    *,
    diagnosis_payload: dict[str, Any],
    service: str,
    container_state: str,
    health_line: str,
    log_lines: list[str] | None,
    runbooks: list[dict[str, Any]],
    history: list[dict[str, Any]],
) -> str:
    parts = [
        f"Service showing symptoms: {service}",
        f"Container state: {container_state} | Health: {health_line}",
        f'Diagnosis: {diagnosis_payload.get("summary", "(none)")}',
        f'Suspected component: {diagnosis_payload.get("suspected_service", "(none)")}',
    ]
    if log_lines:
        # Sirf pehli 6 evidence lines — propose ko logs ka CONTEXT chahiye,
        # poora dump nahi (budget TOKEN_BUDGET §3 Call 2).
        parts.append("Recent error lines:\n" + "\n".join(log_lines[:6]))
    else:
        parts.append("Recent error lines: (none)")
    if runbooks:
        rb_lines = [
            f"[{r['runbook_id']}] (rrf {r['rrf_score']}) {r['title']}\n{r['body']}"
            for r in runbooks
        ]
        parts.append("Retrieved runbooks:\n" + "\n---\n".join(rb_lines))
    else:
        parts.append(
            "Retrieved runbooks: NONE (retrieval was skipped for this incident)."
        )
    parts.append("Previous attempts:\n" + _history_block(history))
    return "\n\n".join(parts)


def run(
    *,
    incident_id: int,
    attempt: int,
    service: str,
    container_state: str,
    health_line: str,
    diagnosis_payload: dict[str, Any],
    log_lines: list[str] | None,
    runbooks: list[dict[str, Any]],
    history: list[dict[str, Any]],
) -> ProposedAction:
    """Ek proposal. Safal ho toh 'propose' event khud likhta hai.

    Raises:
        router.ParseError: 2 baar invalid output (JSON ya allowlist-shape).
            Caller: ESCALATED parse_failed.
        router.LLMUnavailableError: provider down. Caller: ESCALATED
            llm_unavailable.
    """
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(menu=build_menu_text())
    user_prompt = build_user_prompt(
        diagnosis_payload=diagnosis_payload,
        service=service,
        container_state=container_state,
        health_line=health_line,
        log_lines=log_lines,
        runbooks=runbooks,
        history=history,
    )

    def _parse(text: str) -> ProposedAction:
        raw = router.extract_json(text)
        proposal = ProposedAction.model_validate(raw)
        # 🔴 Early shape-check (layers 1–3). Approval screen se PEHLE.
        validate_shape(proposal.action, proposal.params)
        return proposal

    result = router.call(
        incident_id=incident_id,
        attempt=attempt,
        call_site="propose",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )
    try:
        proposal = _parse(result.text)
    except Exception as first_err:  # noqa: BLE001 - retry-with-feedback path
        kind = "allowlist validation" if isinstance(first_err, RejectedError) else "JSON"
        retry_user = (
            f"{user_prompt}\n\nYour previous reply failed {kind} checks: {first_err}. "
            "Reply again with ONLY a corrected JSON object."
        )
        retry = router.call(
            incident_id=incident_id,
            attempt=attempt,
            call_site="propose",
            system_prompt=system_prompt,
            user_prompt=retry_user,
        )
        try:
            proposal = _parse(retry.text)
        except Exception:
            raise router.ParseError(
                f"proposal 2 baar invalid ({kind}); pehla: {first_err}; "
                f"dusra raw: {retry.text[:200]!r}"
            ) from None

    # Citation: top-1 retrieved runbook — UI card isi se banta hai. RAG-off arm
    # mein None, aur wahi N3/N4 comparison ka data hai.
    proposal.runbook_id = runbooks[0]["runbook_id"] if runbooks else None

    record_event(
        incident_id,
        "propose",
        {
            "action": proposal.action,
            "params": dict(proposal.params),
            "reasoning": proposal.reasoning,
            "runbook_id": proposal.runbook_id,
            "provider": result.provider,
            "latency_ms": result.latency_ms,
        },
    )
    return proposal
