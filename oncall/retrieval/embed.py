"""Embeddings — HTTP client + ek chhoti service, DO alag process ke liye.

────────────────────────────────────────────────────────────────────────────
🔴 Embedder alag PROCESS mein kyun hai (D35)
────────────────────────────────────────────────────────────────────────────
`sentence-transformers` PyTorch laata hai: import ALONE ~250–400 MB resident,
model ~90 MB upar. Worker 24/7 chalta hai — uske andar torch rakhna 8 GB
machine pe R3 ka tripwire hai. Isliye:

    embed_service.py  → chhota FastAPI app, port 8010, torch yahan rehta hai
    ye file (embed.py) → HTTP client, torch-free

`rag_enabled=false` ablation arm mein service START HI NAHI hoti — toh wo
~450 MB genuinely kharch nahi hota. Ablation resource-level pe bhi asli hai.

────────────────────────────────────────────────────────────────────────────
CLAUDE.md §3: embeddings EK BAAR compute hote hain, phir cache
────────────────────────────────────────────────────────────────────────────
Query-side embedding har incident mein banta (chhota, 1 text). Corpus-side
embedding sirf `scripts/ingest_runbooks.py` mein banta hai aur `runbooks.
embedding` column mein CACHE hota hai. Koi code-path dobara nahi banata —
"same text ko dobara embed mat karo" isi layout se enforce hota hai.

⚠️ Model pin: `all-MiniLM-L6-v2`, 384 dimensions. Schema ka column
   `vector(384)` isi number se bandha hai — model badla toh migrate karna
   padega. Isliye DIM yahan literal hai, computed nahi.
"""

from __future__ import annotations

import httpx

#: 🔴 Ye number schema (`runbooks.embedding vector(384)`) se match karna
#:    ZARURI hai. Test: tests/test_schema.py dimension check nahi karta (SQL
#:    type hai), lekin ingest + search dono isi se validate karte hain.
DIM = 384

_MODEL = "all-MiniLM-L6-v2"

_TIMEOUT_S = 30.0


class EmbedUnavailableError(RuntimeError):
    """Embed service down / torch install nahi. RAG arm isko ESCALATED nahi
    banne deta — retrieval skip ka decision loop.py leta hai, client nahi."""


def embed(text: str) -> list[float]:
    """Ek text → 384-dim vector. Service down ho toh `EmbedUnavailableError`."""
    vectors = embed_batch([text])
    return vectors[0]


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Batch endpoint use hota hai kyunki ingest ~60 chunks maarta hai —
    60 HTTP round trips ki jagah ek."""
    if not texts:
        return []
    from oncall.config import load_settings

    url = f"{load_settings().embed_url.rstrip('/')}/embed"
    try:
        resp = httpx.post(
            url,
            json={"texts": texts, "model": _MODEL},
            timeout=_TIMEOUT_S,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - transport errors ek hi matlab rakhte hain
        raise EmbedUnavailableError(
            f"embed service ({url}) tak pahunch nahi: {type(exc).__name__}: {exc}. "
            "Chalane ke liye: pip install -r requirements-ingest.txt && "
            "uvicorn oncall.retrieval.embed_service:app --port 8010"
        ) from exc

    vectors = data.get("vectors")
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise EmbedUnavailableError(
            f"embed service ne {len(vectors) if isinstance(vectors, list) else '?'} "
            f"vectors diye, {len(texts)} maange the"
        )
    for v in vectors:
        if len(v) != DIM:
            raise EmbedUnavailableError(
                f"vector {len(v)}-dim aaya, {DIM} chahiye — model badla hai? "
                "(schema vector(384) hai; model badalna = migration)"
            )
    return [list(map(float, v)) for v in vectors]
