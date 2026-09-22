"""Config — saara environment ek jagah, aur ek cheez jaan-boojh kar hardcoded.

Ek hi design point yahan important hai, aur wo ek interview question hai:

    Q: "Listen address env var kyun nahi hai?"
    A: Kyunki Docker socket root-equivalent hai. Is process ke paas host pe
       root-jaisa power hai (THREAT_MODEL.md §2). Agar listen address ek env
       var hoti, toh ek typo — `ONCALL_HOST=0.0.0.0` — is service ko poore
       local network pe expose kar deta, bina kisi auth ke. Wo ek typo ki doori
       pe remote root hai. Isliye wo constant hai, config nahi.

Baaki sab kuch env se aata hai kyunki wo sab environment-specific hai
(URLs, keys, seeds) — lekin security boundary environment-specific nahi hoti.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# 🔴 `dotenv` ka import JAAN-BOOJH KAR optional hai. Wajah do hain, aur dono
#    asli hain:
#
#    (a) `load_dotenv` ka poora kaam itna hai: `.env` file padho, aur usme jo
#        `KEY=value` mile, unhe `os.environ` mein daal do. Bas. Agar library
#        maujood nahi hai, toh `os.environ` **phir bhi kaam karta hai** — shell
#        se export kiya hua var, `docker compose` ka `environment:` block, CI ka
#        secrets — sab as-is milte hain. Yaani ye ek *convenience* hai,
#        requirement nahi. Aur convenience ko hard dependency banana galat hai.
#
#    (b) Isse `oncall.config` **zero third-party deps ke saath** import ho jaata
#        hai. Ye seedha kaam ka farq hai: `oncall/actions/broker.py` (security
#        boundary) ko yahan se `MAX_ATTEMPTS` chahiye. Agar ye import fail kare,
#        toh broker bhi import nahi ho sakta, aur uske adversarial tests
#        `pip install` ke baad hi chalte — matlab jo layer sabse zyada test
#        maangti hai, wahi sabse der se test hoti.
#
#    ⚠️ Trade-off imaandaari se: `.env` chupchaap ignore ho sakti hai agar
#       library missing ho aur user ko lage ki wo load hui. Isliye
#       `DOTENV_LOADED` flag neeche export kiya jaata hai — `scripts/doctor.py`
#       aur startup log isse padh kar saaf bata sakte hain ki file padhi gayi
#       ya nahi. Silent fallback theek hai; *invisible* silent fallback nahi.
#
#    Rejected alternative: `dotenv` ko requirements se hata kar khud 15-line
#    parser likhna. Quoting, `export ` prefix, multi-line values, `#` comments —
#    ye sab edge cases dobara solve karne padte, aur ek buggy parser missing
#    library se bura hai.
try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - depends on install state
    load_dotenv = None  # type: ignore[assignment]

# Repo root = is file se do level upar (oncall/config.py -> oncall/ -> repo/)
REPO_ROOT = Path(__file__).resolve().parent.parent

# `.env` load karo agar hai. `override=False` ka matlab: jo variable pehle se
# shell mein set hai, wo jeetega. Ye CI aur one-off runs ke liye zaruri hai —
# `ONCALL_RAG_ENABLED=false make eval` kaam karna chahiye bina .env chhede.
#: ✅ `True` = `.env` padhi gayi. `False` = python-dotenv install nahi hai, toh
#: sirf asli environment variables use ho rahe hain.
DOTENV_LOADED: bool = load_dotenv is not None
if load_dotenv is not None:
    load_dotenv(REPO_ROOT / ".env", override=False)


# ─────────────────────────────────────────────────────────────────────────────
# 🔒 Hardcoded — ye env se NAHI aate
# ─────────────────────────────────────────────────────────────────────────────

#: Control plane sirf loopback pe sunta hai. Ye constant hai, setting nahi.
LISTEN_HOST = "127.0.0.1"

#: FastAPI control plane ka port.
LISTEN_PORT = 8000

#: Oracle ka contract (DECISIONS.md D2). Teeno numbers yahan ek saath hain
#: taaki koi inko "tune" karke oracle ko chupchaap kamzor na kar de.
ORACLE_PROBES = 3  #: kitni baar check karna hai
ORACLE_PROBE_GAP_S = 10  #: probes ke beech gap → 3 × 10 = 30 second window
ORACLE_LOAD_RPS = 20  #: har probe se pehle kitna load. Load ke bina F4 chhup jaata hai
ORACLE_PROBE_TIMEOUT_S = 5.0  #: ek probe kitna wait karega

#: Ek incident mein max attempts. 4th attempt nahi hoti — ESCALATED hota hai.
MAX_ATTEMPTS = 3


# ─────────────────────────────────────────────────────────────────────────────
# Env se aane wali cheezein
# ─────────────────────────────────────────────────────────────────────────────


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_bool(key: str, default: bool) -> bool:
    """"true"/"1"/"yes" → True. Baaki sab False.

    Deliberately strict-ish: `ONCALL_RAG_ENABLED=maybe` False deta hai, error
    nahi — kyunki ablation script isko loop mein set karti hai aur ek crash
    poora run maar deta.
    """
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"true", "1", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Ek immutable snapshot. `frozen=True` ka matlab koi ise runtime pe badal
    nahi sakta — config ek boot-time fact hai, ek mutable global nahi."""

    # Databases
    oncall_db_url: str
    shopdb_url: str

    # Monitored service
    shopapp_url: str

    # LLM
    groq_api_key: str
    groq_model: str
    gemini_api_key: str
    gemini_model: str
    openrouter_api_key: str
    openrouter_model: str
    pin_provider: str

    # Retrieval
    embed_url: str
    top_k: int

    # Ablation + reproducibility
    rag_enabled: bool
    seed: int

    @property
    def shopapp_health_url(self) -> str:
        return f"{self.shopapp_url.rstrip('/')}/health"


def load_settings() -> Settings:
    """Env padho aur ek Settings banao. Har call fresh padhta hai — isliye
    ablation script `os.environ` badal kar dobara call kar sakti hai."""
    return Settings(
        oncall_db_url=_env(
            "ONCALL_DB_URL", "postgresql://oncall:oncall@127.0.0.1:5433/oncall"
        ),
        shopdb_url=_env("SHOPDB_URL", "postgresql://shop:shop@127.0.0.1:5432/shop"),
        shopapp_url=_env("SHOPAPP_URL", "http://127.0.0.1:8001"),
        groq_api_key=_env("GROQ_API_KEY", ""),
        # 🔴 2026-08-24: purana default `llama-3.3-70b-versatile` Groq ne RETIRE kar diya
        # (404 live mila). Naya default unka flagship instruct model hai.
        groq_model=_env("GROQ_MODEL", "openai/gpt-oss-120b"),
        gemini_api_key=_env("GEMINI_API_KEY", ""),
        # Fallback models: Gemini ka OpenAI-compatible endpoint aur OpenRouter
        # dono model NAME bhi maangte hain. Defaults free-tier-friendly hain;
        # ⚠️ dono providers ke limits UNVERIFIED hain (RISKS.md R9) — isliye
        # in keys khaali rehti hain jab tak verify na ho.
        gemini_model=_env("GEMINI_MODEL", "gemini-2.0-flash"),
        openrouter_api_key=_env("OPENROUTER_API_KEY", ""),
        openrouter_model=_env(
            "OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"
        ),
        pin_provider=_env("ONCALL_PIN_PROVIDER", "groq"),
        embed_url=_env("ONCALL_EMBED_URL", "http://127.0.0.1:8010"),
        top_k=_env_int("ONCALL_TOP_K", 3),
        rag_enabled=_env_bool("ONCALL_RAG_ENABLED", True),
        seed=_env_int("ONCALL_SEED", 1337),
    )


#: Module-level default. Jo code ise import karta hai, use har baar
#: `load_settings()` nahi likhna padta.
settings = load_settings()
