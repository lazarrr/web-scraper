"""
reranker.py — Cross-encoder reranker for the search pipeline.

After hybrid retrieval (dense + sparse → RRF) produces a pool of top-N
candidates, a cross-encoder evaluates each (query, document) pair jointly
and picks the best k for the final response.

Default model: BAAI/bge-reranker-v2-m3 — multilingual, supports Serbian.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from sentence_transformers import CrossEncoder

if TYPE_CHECKING:
    from chromadb import PersistentClient

logger = logging.getLogger("reranker")


class CrossEncoderReranker:
    """Wraps a sentence-transformers CrossEncoder for document reranking."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        device: str = "cpu",
        max_length: int = 512,
    ):
        self._model_name = model_name
        self._device = device
        self._max_length = max_length
        self._model: CrossEncoder | None = None
        self._ready = False

    def load(self) -> None:
        logger.info("Loading cross-encoder '%s' on %s ...", self._model_name, self._device)
        try:
            self._model = CrossEncoder(
                self._model_name,
                device=self._device,
                max_length=self._max_length,
            )
            self._ready = True
            logger.info("Cross-encoder ready.")
        except Exception as exc:
            logger.error("Failed to load cross-encoder: %s", exc)
            self._ready = False

    def is_ready(self) -> bool:
        return self._ready and self._model is not None

    def rerank(
        self,
        query: str,
        documents: list[tuple[str, str, dict]],  # (doc_id, text, metadata)
        top_k: int = 5,
        batch_size: int = 32,
    ) -> list[tuple[str, str, dict, float]]:
        """Score (query, doc) pairs and return top_k with cross-encoder scores.

        Parameters
        ----------
        query : user query string
        documents : list of (doc_id, document_text, metadata)
        top_k : number of results to return
        batch_size : batch size for the cross-encoder

        Returns
        -------
        list of (doc_id, text, metadata, score) sorted descending by score.
        """
        if not self.is_ready():
            logger.warning("Reranker not ready — returning documents unranked.")
            return [(d[0], d[1], d[2], 0.0) for d in documents[:top_k]]

        if not documents:
            return []

        pairs = [(query, doc[1]) for doc in documents]
        scores = self._model.predict(
            pairs,
            batch_size=batch_size,
            show_progress_bar=len(pairs) > 100,
        )

        if isinstance(scores, (int, float)):
            scores = [float(scores)]

        ranked: list[tuple] = [
            (documents[i][0], documents[i][1], documents[i][2], float(scores[i]))
            for i in range(len(documents))
        ]
        ranked.sort(key=lambda x: x[3], reverse=True)
        return ranked[:top_k]


def rerank_with_cross_encoder(
    query: str,
    candidates: list[tuple[str, str, dict]],
    reranker: CrossEncoderReranker,
    top_k: int = 5,
) -> list[tuple[str, str, dict, float]]:
    """Convenience wrapper that guards against an unloaded reranker."""
    if not reranker.is_ready():
        return [(c[0], c[1], c[2], 0.0) for c in candidates[:top_k]]
    return reranker.rerank(query, candidates, top_k=top_k)
