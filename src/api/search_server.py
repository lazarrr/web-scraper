"""
search_server.py — FastAPI server that exposes vector-DB similarity search
for the Flutter test UI.  Reads the same ChromaDB collection that the
scraper populates, using the shared config for model/prefix/space settings.
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

# ------------------------------------------------------------------ #
#  Logging
# ------------------------------------------------------------------ #
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("search_server")

# ------------------------------------------------------------------ #
#  FastAPI app
# ------------------------------------------------------------------ #
app = FastAPI(title="Vector Search API", version="1.0.0")

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

# Resolve VECTOR_DB_PATH relative to the project root (not CWD),
# so the search server always uses the same DB as run.py.
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

# Ensure the collection exists (get_or_create — safe when scraper hasn't ingested yet)
_collection = _client.get_or_create_collection(
    name=_collection_name,
    metadata={"hnsw:space": _shared["hnsw_space"]},
)

if _collection.count() == 0:
    logger.warning(
        "Collection '%s' is empty. Run the scraper ingest to populate the vector store.",
        _collection_name,
    )

logger.info("==============================")
logger.info(
    "Search server ready — collection='%s', count=%d, model='%s', persist='%s' (absolute: %s)",
    _collection_name,
    _collection.count(),
    _shared["embedding_model"],
    _vectordb_path,
    os.path.abspath(_vectordb_path),
)
logger.info("==============================")

# ------------------------------------------------------------------ #
#  Pydantic models
# ------------------------------------------------------------------ #
class SearchRequest(BaseModel):
    query: str
    k: int = 5


class SearchResultItem(BaseModel):
    document: str
    metadata: dict
    distance: float
    similarity: float  # 1.0 - distance  (cosine similarity)


class SearchResponse(BaseModel):
    query: str
    total_results: int
    results: list[SearchResultItem]


# ------------------------------------------------------------------ #
#  Endpoints
# ------------------------------------------------------------------ #
@app.post("/search", response_model=SearchResponse)
async def similarity_search(request: SearchRequest):
    """
    Perform similarity search against the ChromaDB vector store.

    Returns matching documents ordered by similarity (highest first),
    along with metadata, raw cosine distance, and a similarity score
    in the [0, 1] range (1 = identical, 0 = opposite).
    """
    try:
        # Embed the query with the E5 *query* prefix
        prefixed = [_shared["query_prefix"] + request.query]
        query_embedding = _embedding_model.encode(
            prefixed,
            normalize_embeddings=_normalize,
        )[0]

        results = _collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=request.k,
            include=["documents", "metadatas", "distances"],
        )

        items: list[SearchResultItem] = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            if _distance_threshold > 0 and dist > _distance_threshold:
                continue  # filter below similarity threshold
            items.append(SearchResultItem(
                document=doc,
                metadata=meta or {},
                distance=round(dist, 4),
                similarity=round(1.0 - dist, 4),
            ))

        return SearchResponse(
            query=request.query,
            total_results=len(items),
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
        return {"status": "healthy", "document_count": count}
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
