"""
bm25_index.py — BM25 sparse index for hybrid search.

Built from the ChromaDB collection on search-server startup and stored
in-process.  Combined with dense vector scores via Reciprocal Rank Fusion
to improve retrieval of exact terms (course codes, names, dates) that
embeddings often miss, especially in Serbian.

Uses the `rank_bm25` library (pure Python, no extra system deps).
"""

from __future__ import annotations

import os
import pickle
import logging

import numpy as np
from rank_bm25 import BM25Okapi

from chromadb import PersistentClient

logger = logging.getLogger("bm25_index")

# Simple unicode-aware tokenizer that handles Serbian characters
import re

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _default_tokenizer(text: str) -> list[str]:
    """Tokenize text, lowercasing and keeping Unicode word characters."""
    return _TOKEN_RE.findall(text.lower())


class BM25Index:
    """In-memory BM25 index backed by a ChromaDB collection."""

    def __init__(
        self,
        chroma_client: PersistentClient,
        collection_name: str,
        persist_dir: str,
    ):
        self._client = chroma_client
        self._collection_name = collection_name
        self._persist_path = os.path.join(persist_dir, "bm25_index.pkl")

        self._corpus: list[str] = []
        self._doc_ids: list[str] = []
        self._index: BM25Okapi | None = None
        self._ready = False

    # ------------------------------------------------------------------ #
    #  Build / load                                                        #
    # ------------------------------------------------------------------ #

    def build(self) -> None:
        """Read all documents from ChromaDB and build the BM25 index."""
        try:
            collection = self._client.get_collection(self._collection_name)
        except Exception:
            logger.warning("ChromaDB collection '%s' not found.", self._collection_name)
            return

        total = collection.count()
        if total == 0:
            logger.warning("Collection '%s' is empty — BM25 index will be empty.", self._collection_name)
            return

        logger.info("Fetching %d documents for BM25 index ...", total)

        # Batch-fetch all documents
        batch_size = 500
        self._corpus = []
        self._doc_ids = []

        for offset in range(0, total, batch_size):
            results = collection.get(
                offset=offset,
                limit=batch_size,
                include=["documents"],
            )
            if results["ids"] and results["documents"]:
                self._corpus.extend(results["documents"])
                self._doc_ids.extend(results["ids"])

        tokenized = [_default_tokenizer(doc) for doc in self._corpus]
        self._index = BM25Okapi(tokenized)
        self._ready = True

        logger.info("BM25 index ready — %d documents indexed.", len(self._corpus))

    def save(self) -> None:
        """Persist the corpus and doc IDs to disk.  The index itself is
        cheap to rebuild, so we only save the raw data."""
        if not self._corpus:
            logger.debug("BM25 corpus is empty — nothing to save.")
            return
        data = {"corpus": self._corpus, "doc_ids": self._doc_ids}
        with open(self._persist_path, "wb") as fh:
            pickle.dump(data, fh)
        logger.info("BM25 corpus saved to %s (%d docs).", self._persist_path, len(self._corpus))

    def load(self) -> None:
        """Load the corpus from disk and rebuild the BM25 index."""
        if not os.path.exists(self._persist_path):
            logger.info("No BM25 index file found at %s — building from ChromaDB.", self._persist_path)
            self.build()
            self.save()
            return

        with open(self._persist_path, "rb") as fh:
            data = pickle.load(fh)

        self._corpus = data["corpus"]
        self._doc_ids = data["doc_ids"]
        tokenized = [_default_tokenizer(doc) for doc in self._corpus]
        self._index = BM25Okapi(tokenized)
        self._ready = True
        logger.info("BM25 index loaded from %s (%d docs).", self._persist_path, len(self._corpus))

    def is_ready(self) -> bool:
        return self._ready and self._index is not None

    # ------------------------------------------------------------------ #
    #  Search                                                              #
    # ------------------------------------------------------------------ #

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        """Return top_k (doc_id, bm25_score) pairs, sorted by score desc."""
        if not self.is_ready():
            return []

        tokenized = _default_tokenizer(query)
        scores = self._index.get_scores(tokenized)

        # Get indices of top_k scores
        if top_k >= len(scores):
            top_indices = np.argsort(scores)[::-1]
        else:
            top_indices = np.argpartition(scores, -top_k)[-top_k:]
            top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]

        results: list[tuple[str, float]] = []
        for idx in top_indices:
            score = float(scores[idx])
            if score > 0:
                results.append((self._doc_ids[idx], score))

        return results


# ------------------------------------------------------------------ #
#  Reciprocal Rank Fusion (RRF)                                       #
# ------------------------------------------------------------------ #

def reciprocal_rank_fusion(
    dense_results: list[tuple[str, float, int]],
    sparse_results: list[tuple[str, float, int]],
    k: int = 60,
    dense_weight: float = 0.5,
    sparse_weight: float = 0.5,
) -> list[tuple[str, float]]:
    """Combine dense and sparse ranked lists with weighted RRF.

    Parameters
    ----------
    dense_results : list of (doc_id, similarity, rank) from vector search
    sparse_results : list of (doc_id, bm25_score, rank) from BM25
    k : RRF compensation constant (default 60, per research literature)
    dense_weight, sparse_weight : weights for each signal (default 0.5 each)

    Returns
    -------
    list of (doc_id, fused_score) sorted descending by fused score.
    """
    scores: dict[str, float] = {}

    for doc_id, _, rank in dense_results:
        rrf_score = 1.0 / (k + rank + 1)
        scores[doc_id] = scores.get(doc_id, 0.0) + dense_weight * rrf_score

    for doc_id, _, rank in sparse_results:
        rrf_score = 1.0 / (k + rank + 1)
        scores[doc_id] = scores.get(doc_id, 0.0) + sparse_weight * rrf_score

    fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return fused
