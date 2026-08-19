"""
search_server.py -- FastAPI server that exposes hybrid vector search
(dense + BM25 sparse) with optional cross-encoder reranking for the
Flutter test UI.  Reads the same ChromaDB collection that the scraper
populates, using the shared config for model/prefix/space settings.
"""

from __future__ import annotations

import json
import logging
import os
import sys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Make sure the university-scraper package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import chromadb
import numpy as np
from chromadb.config import Settings as ChromaSettings
from sentence_transformers import SentenceTransformer

from config.settings import settings
from src.core.bm25_index import BM25Index, reciprocal_rank_fusion
from src.core.reranker import CrossEncoderReranker

# ------------------------------------------------------------------ #
#  Logging
# ------------------------------------------------------------------ #
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("search_server")

# ------------------------------------------------------------------ #
#  FastAPI app
# ------------------------------------------------------------------ #
app = FastAPI(title="Vector Search API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------ #
#  Shared defaults (must match chatbot & scraper)
# ------------------------------------------------------------------ #
_DEFAULTS: dict = {
    "collection_name": "university_knowledge",
    "embedding_model": "intfloat/multilingual-e5-large",
    "embedding_device": "cpu",
    "normalize_embeddings": True,
    "query_prefix": "query: ",
    "passage_prefix": "passage: ",
    "hnsw_space": "cosine",
}


def _load_shared_config(persist_dir: str) -> dict:
    """Load shared_config.json, falling back to hard-coded defaults."""
    path = os.path.join(persist_dir, "shared_config.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            return {**_DEFAULTS, **loaded}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not parse %s: %s. Using defaults.", path, exc)
    else:
        logger.warning("shared_config.json not found at %s. Using defaults.", path)
    return dict(_DEFAULTS)


# ------------------------------------------------------------------ #
#  Init vector store (read-only)
# ------------------------------------------------------------------ #

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_vectordb_path = os.path.join(_PROJECT_ROOT, settings.VECTOR_DB_PATH.lstrip("./"))

_shared = _load_shared_config(_vectordb_path)

_embedding_model = SentenceTransformer(
    _shared["embedding_model"],
    device=_shared["embedding_device"],
)
_normalize = _shared["normalize_embeddings"]
_collection_name = _shared["collection_name"]
_distance_threshold = 1.0 - getattr(settings, "SIMILARITY_THRESHOLD", 0.0)

_client = chromadb.PersistentClient(
    path=_vectordb_path,
    settings=ChromaSettings(anonymized_telemetry=False),
)

_collection = _client.get_or_create_collection(
    name=_collection_name,
    metadata={"hnsw:space": _shared["hnsw_space"]},
)

# ------------------------------------------------------------------ #
#  Hybrid search setup
# ------------------------------------------------------------------ #
_hybrid_enabled = getattr(settings, "HYBRID_SEARCH_ENABLED", False)
_candidate_count = getattr(settings, "HYBRID_CANDIDATE_COUNT", 20)

_bm25: BM25Index | None = None
if _hybrid_enabled:
    _bm25 = BM25Index(_client, _collection_name, _vectordb_path)
    _bm25.load()

# ------------------------------------------------------------------ #
#  Reranker setup
# ------------------------------------------------------------------ #
_reranker_enabled = getattr(settings, "RERANKER_ENABLED", False)
_reranker: CrossEncoderReranker | None = None
if _reranker_enabled:
    _reranker = CrossEncoderReranker(
        model_name=getattr(settings, "RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"),
        device=getattr(settings, "RERANKER_DEVICE", "cpu"),
        max_length=getattr(settings, "RERANKER_MAX_LENGTH", 512),
    )
    _reranker.load()

if _collection.count() == 0:
    logger.warning(
        "Collection '%s' is empty. Run the scraper ingest to populate the vector store.",
        _collection_name,
    )

logger.info("==============================")
logger.info(
    "Search server ready -- collection='%s', count=%d, model='%s', persist='%s'",
    _collection_name,
    _collection.count(),
    _shared["embedding_model"],
    _vectordb_path,
)
logger.info(
    "Features: hybrid=%s, reranker=%s",
    _hybrid_enabled,
    _reranker_enabled,
)
logger.info("==============================")


def _dense_search(query_embedding: np.ndarray, top_k: int) -> list[tuple[str, float, int]]:
    """Pure dense vector search. Returns (doc_id, similarity, rank)."""
    results = _collection.query(
        query_embeddings=[query_embedding.tolist()],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    docs = results.get("documents", [[]])[0] or []
    metas = results.get("metadatas", [[]])[0] or []
    dists = results.get("distances", [[]])[0] or []
    ids = results.get("ids", [[]])[0] or []

    out: list[tuple[str, float, int]] = []
    for rank, (doc_id, text, meta, dist) in enumerate(zip(ids, docs, metas, dists)):
        sim = round(1.0 - float(dist), 4)
        if _distance_threshold > 0 and float(dist) > _distance_threshold:
            continue
        out.append((doc_id, sim, rank))
    return out


def _resolve_document(
    doc_id: str,
    dense_cache: dict[str, tuple[str, dict]],
    bm25_cache: dict[str, tuple[str, dict]],
) -> tuple[str, str, dict] | None:
    """Look up a doc_id in the caches.  Prefer dense cache (has full metadata)."""
    if doc_id in dense_cache:
        text, meta = dense_cache[doc_id]
        return (doc_id, text, meta)
    if doc_id in bm25_cache:
        text, meta = bm25_cache[doc_id]
        return (doc_id, text, meta)
    return None


# ------------------------------------------------------------------ #
#  Pydantic models
# ------------------------------------------------------------------ #
class SearchRequest(BaseModel):
    query: str
    k: int = 5


class SearchResultItem(BaseModel):
    document: str
    metadata: dict
    distance: float | None = None
    similarity: float | None = None
    cross_encoder_score: float | None = None


class SearchResponse(BaseModel):
    query: str
    total_results: int
    retrieval_method: str
    results: list[SearchResultItem]


# ------------------------------------------------------------------ #
#  Endpoints
# ------------------------------------------------------------------ #
@app.post("/search", response_model=SearchResponse)
async def similarity_search(request: SearchRequest):
    """
    Hybrid search (dense + BM25) with optional cross-encoder reranking.

    1. Encode query with E5 prefix
    2. Dense vector search (top-N)
    3. BM25 sparse search (top-N) [if enabled]
    4. Reciprocal Rank Fusion to combine signals
    5. Cross-encoder rerank final pool [if enabled]
    6. Return top-k results
    """
    try:
        prefixed = [_shared["query_prefix"] + request.query]
        query_embedding = _embedding_model.encode(
            prefixed,
            normalize_embeddings=_normalize,
        )[0]

        retrieval_k = max(request.k, _candidate_count)

        # -- Dense search --------------------------------------------- #
        dense_results = _dense_search(query_embedding, retrieval_k)
        dense_cache: dict[str, tuple[str, dict]] = {}

        # Also resolve text/metadata for dense results
        if dense_results:
            ids_only = [r[0] for r in dense_results]
            resolved = _collection.get(
                ids=ids_only,
                include=["documents", "metadatas"],
            )
            for did, text, meta in zip(
                resolved.get("ids", []) or [],
                resolved.get("documents", []) or [],
                resolved.get("metadatas", []) or [],
            ):
                dense_cache[did] = (text, meta or {})

        # -- Sparse search -------------------------------------------- #
        sparse_results: list[tuple[str, float, int]] = []
        bm25_cache: dict[str, tuple[str, dict]] = {}
        if _hybrid_enabled and _bm25 and _bm25.is_ready():
            bm25_hits = _bm25.search(request.query, top_k=retrieval_k)
            for rank, (doc_id, score) in enumerate(bm25_hits):
                sparse_results.append((doc_id, score, rank))
            # Resolve BM25-only texts from ChromaDB
            bm25_only_ids = [
                r[0] for r in sparse_results if r[0] not in dense_cache
            ]
            if bm25_only_ids:
                resolved = _collection.get(
                    ids=bm25_only_ids,
                    include=["documents", "metadatas"],
                )
                for did, text, meta in zip(
                    resolved.get("ids", []) or [],
                    resolved.get("documents", []) or [],
                    resolved.get("metadatas", []) or [],
                ):
                    bm25_cache[did] = (text, meta or {})

        # -- Fuse ----------------------------------------------------- #
        if _hybrid_enabled and sparse_results:
            fused = reciprocal_rank_fusion(
                dense_results,
                sparse_results,
                k=settings.RRF_K,
                dense_weight=settings.RRF_DENSE_WEIGHT,
                sparse_weight=settings.RRF_SPARSE_WEIGHT,
            )
            method = "hybrid_dense_bm25"
        else:
            fused = [(r[0], float(r[1])) for r in dense_results]
            fused.sort(key=lambda x: x[1], reverse=True)
            method = "dense_only"

        # Build candidate list for reranking
        candidates: list[tuple[str, str, dict]] = []
        for doc_id, _ in fused[:retrieval_k]:
            resolved = _resolve_document(doc_id, dense_cache, bm25_cache)
            if resolved:
                candidates.append(resolved)

        # -- Rerank --------------------------------------------------- #
        final_results: list[tuple[str, str, dict, float | None]] = []
        if _reranker_enabled and _reranker and _reranker.is_ready() and len(candidates) > request.k:
            ranked = _reranker.rerank(
                request.query,
                candidates,
                top_k=request.k,
            )
            final_results = [(r[0], r[1], r[2], round(r[3], 4)) for r in ranked]
            method += "+reranker"
        else:
            final_results = [(c[0], c[1], c[2], None) for c in candidates[:request.k]]

        # -- Build response ------------------------------------------- #
        items: list[SearchResultItem] = []
        for doc_id, text, meta, ce_score in final_results:
            dist = None
            sim = None
            if doc_id in dense_cache:
                sim = next((r[1] for r in dense_results if r[0] == doc_id), None)
                dist = round(1.0 - sim, 4) if sim is not None else None
            items.append(SearchResultItem(
                document=text,
                metadata=meta,
                distance=dist,
                similarity=sim,
                cross_encoder_score=ce_score,
            ))

        return SearchResponse(
            query=request.query,
            total_results=len(items),
            retrieval_method=method,
            results=items,
        )

    except Exception as e:
        logger.exception("Search failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Quick health-check endpoint."""
    try:
        count = _collection.count()
        bm25_ok = _bm25 is not None and _bm25.is_ready() if _hybrid_enabled else None
        reranker_ok = _reranker is not None and _reranker.is_ready() if _reranker_enabled else None
        return {
            "status": "healthy",
            "document_count": count,
            "hybrid_enabled": _hybrid_enabled,
            "bm25_ready": bm25_ok,
            "reranker_enabled": _reranker_enabled,
            "reranker_ready": reranker_ok,
        }
    except Exception:
        return {"status": "healthy", "document_count": 0}


# ------------------------------------------------------------------ #
#  Main
# ------------------------------------------------------------------ #
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.api.search_server:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
    )
