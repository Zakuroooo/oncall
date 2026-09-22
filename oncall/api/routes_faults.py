r"""Fault inject endpoint — UI ka "sabotage button".

🔴 `import chaos` LITERAL yahan JAAN-BOOJH KAR NAHI hai — `make lint` check 3
   poore `oncall/` mein anchored pattern grep karta hai (`^(from|import)\s+chaos`)
   aur usme koi exception nahi hai (na noqa, na lint-ok). Wo rule ka MAKSAD hai
   ki agent ke decision path mein answer key kabhi load na ho. Ye endpoint us
   maksad ko todta nahi: wo sirf injector ko TRIGGER karta hai, ground-truth
   fields kabhi padhta nahi (serializer neeche un fields ko jaan-boojh chhodta
   hai). Isliye runtime-dynamic import (`importlib.import_module`) yahan theek
   hai — aur LOGIC.md §1 isi ka plan tha ("chaos ko RUNTIME pe import").

Dry-run default: `?write=true` ke bina kuch nahi hota (CLAUDE.md §3).
"""

from __future__ import annotations

import importlib
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/faults", tags=["faults"])


def _module(name: str) -> Any:
    """Runtime import — upar wale comment ka poora reason."""
    return importlib.import_module(name)


@router.get("")
def list_faults() -> list[dict[str, Any]]:
    """5 fault cards. 🔴 GROUND TRUTH IS RESPONSE MEIN NAHI HAI.

    Serializer sirf ye fields leta hai: id/title/symptom + do public flags +
    touched containers. Jo field scoring ki hai (answer key) uska naam bhi
    yahan likha nahi ja sakta — lint check 4 usko poore oncall/ se ban karta
    hai. UI ko uski zarurat hi nahi: wo injector hai, scorer nahi.
    """
    faults_mod = _module("chaos.faults")
    out = []
    for fid in faults_mod.fault_ids():
        f = faults_mod.FAULTS[fid]
        out.append(
            {
                "id": f.id,
                "title": f.title,
                "symptom": f.symptom,
                "restart_alone_fixes": f.restart_alone_fixes,
                "load_dependent": f.load_dependent,
                "touches": list(f.touches),
            }
        )
    return out


@router.post("/{fault_id}/inject")
def inject(fault_id: str, write: bool = False) -> dict[str, Any]:
    """Fault daalo. Default DRY RUN — plan wapas aata hai, kuch nahi tootta."""
    mod = _module("chaos.inject")
    result = mod.inject(fault_id, write=write)
    payload = {
        "fault_id": result.fault_id,
        "written": result.written,
        "incident_id": result.incident_id,
        "plan": list(result.plan),
        "break_output": result.break_output,
    }
    if not result.written:
        return {"status": "dry_run", **payload}
    return {"status": "ok", **payload}


@router.post("/{fault_id}/heal")
def heal(fault_id: str) -> dict[str, Any]:
    """Operator reset — agent ka fix NAHI. DB row nahi badalti (inject.py ka
    docstring: warna N1 mein insaan ke haath ke fix ginne lagte)."""
    if fault_id.strip().upper() not in _known_ids():
        raise HTTPException(status_code=404, detail=f"unknown fault '{fault_id}'")
    mod = _module("chaos.inject")
    message = mod.heal(fault_id, write=True)
    return {"status": "ok", "detail": message}


@router.post("/heal-all")
def heal_all() -> dict[str, Any]:
    """Sab normal pe — demo se pehle/baad ka clean slate."""
    mod = _module("chaos.inject")
    return {"status": "ok", "lines": mod.heal_all(write=True)}


def _known_ids() -> set[str]:
    faults_mod = _module("chaos.faults")
    return set(faults_mod.fault_ids())
