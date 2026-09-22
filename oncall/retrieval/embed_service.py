"""Embed service — torch yahan REHTA hai, worker mein nahi (D35).

Chalane ka tareeka:
    make install-ingest          # ~2.5 GB download — ek hi baar
    .venv/bin/uvicorn oncall.retrieval.embed_service:app --host 127.0.0.1 --port 8010

🔴 `sentence_transformers` ka import JAAN-BOOJH KAR lifespan ke andar hai.
   Top-level hota toh sirf MODULE IMPORT karna bhi torch khol deta — matlab
   `pytest` collection, `--help`, har chhoti script 400 MB RAM ke saath boot
   hoti. Lazy import ke saath bina-ingest-deps wala poora project untouched
   rehta hai.

🔴 Bind address hardcoded 127.0.0.1 — wahi rule jo control plane ka hai. Ye
   service koi auth nahi karta; network pe expose hui toh koi bhi compute
   chor sakta hai (aur model tak pahunch jaata).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

_MODEL = "all-MiniLM-L6-v2"
_DIM = 384


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=200)
    model: str = _MODEL


class EmbedResponse(BaseModel):
    model: str
    dim: int
    vectors: list[list[float]]


@asynccontextmanager
async def lifespan(app: FastAPI):
    from sentence_transformers import SentenceTransformer

    app.state.model = SentenceTransformer(_MODEL)
    yield
    app.state.model = None


app = FastAPI(title="oncall-embed", lifespan=lifespan)


@app.post("/embed")
async def embed(req: EmbedRequest) -> EmbedResponse:
    if req.model != _MODEL:
        # 🔴 Model mismatch loud fail — chup-chaap doosre model ka vector
        #    384-dim column mein likhna corpus ko permanently corrupt kar deta.
        raise ValueError(
            f"model '{req.model}' serve nahi hota; pinned model '{_MODEL}' hai "
            "(schema vector(384) isi se bandha hai)"
        )
    model = getattr(app.state, "model", None)
    if model is None:
        raise RuntimeError("model load nahi hua — startup adhoora hai")
    vectors: Any = model.encode(req.texts, normalize_embeddings=True)
    return EmbedResponse(
        model=_MODEL,
        dim=_DIM,
        vectors=[list(map(float, v)) for v in vectors],
    )


@app.get("/health")
async def health() -> dict[str, str]:
    loaded = getattr(app.state, "model", None) is not None
    return {"status": "ok" if loaded else "loading", "model": _MODEL}
