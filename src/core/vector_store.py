"""
vector_store.py -- Write-only ChromaDB vector store for university-scraper.

Reads shared_config.json from the persist directory to ensure
the embedding model, collection name, prefixes, and HNSW settings
match exactly what university-chatbot expects.

Capabilities (WRITE side only):
  • Load SentenceTransformer with the configured embedding model
  • Create embeddings with the E5 "passage: " prefix
  • Upsert documents into the Chroma collection (idempotent via content hash)
  • Check if a collection is populated
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

import chromadb
import numpy as np
from chromadb.config import Settings as ChromaSettings
from sentence_transformers import SentenceTransformer
from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.processing.document_loader import DocumentProcessor


class VectorStoreManager:
    """
    Write-only vector store for the scraper.

    All shared constants (model name, collection name, prefixes,
    HNSW space, normalisation) are read from shared_config.json
    in the persist directory.  If the file is missing the manager
    falls back to hard-coded defaults that match the chatbot project.
    """

    # ------------------------------------------------------------------ #
    #  Fallback defaults (must match university-chatbot exactly)           #
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

    def __init__(self, config):
        self.config = config

        # ── Load shared config from the persist directory ─────────── #
        self._shared = self._load_shared_config(config.VECTOR_DB_PATH)

        # ── SentenceTransformer ───────────────────────────────────── #
        self.embeddings_model = SentenceTransformer(
            self._shared["embedding_model"],
            device=self._shared["embedding_device"],
        )
        self._normalize = self._shared["normalize_embeddings"]

        # ── ChromaDB client ───────────────────────────────────────── #
        self.client = chromadb.PersistentClient(
            path=config.VECTOR_DB_PATH,
            settings=ChromaSettings(anonymized_telemetry=False),
        )

        # ── Text splitter ─────────────────────────────────────────── #
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.CHUNK_SIZE,
            chunk_overlap=config.CHUNK_OVERLAP,
            length_function=len,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

        # ── Internal file loader (DOCX + PDF with header stripping) ─ #
        self._loader = DocumentProcessor(
            chunk_size=config.CHUNK_SIZE,
            chunk_overlap=config.CHUNK_OVERLAP,
        )

        self._collection_name = self._shared["collection_name"]

    # ------------------------------------------------------------------ #
    #  Shared config                                                        #
    # ------------------------------------------------------------------ #

    @classmethod
    def _load_shared_config(cls, persist_dir: str) -> dict:
        """Load shared_config.json, falling back to hard-coded defaults."""
        path = os.path.join(persist_dir, "shared_config.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                merged = {**cls._DEFAULTS, **loaded}
                print(f"Loaded shared config from {path}")
                return merged
            except (json.JSONDecodeError, OSError) as exc:
                print(f"Warning: could not parse {path}: {exc}.  Using defaults.")
        else:
            print(
                f"Warning: {path} not found.  Using hard-coded defaults.  "
                f"Run this scraper once to generate it, or create it manually."
            )
        return dict(cls._DEFAULTS)

    # ------------------------------------------------------------------ #
    #  Embeddings                                                          #
    # ------------------------------------------------------------------ #

    @property
    def PASSAGE_PREFIX(self) -> str:
        """The E5 passage prefix — only used for indexing."""
        return self._shared["passage_prefix"]

    @property
    def QUERY_PREFIX(self) -> str:
        """The E5 query prefix — NOT used by the scraper, but present for
        completeness so that shared_config.json is the single source of truth."""
        return self._shared["query_prefix"]

    def create_embeddings(self, texts: list[str]) -> np.ndarray:
        """
        Encode texts with the E5 *passage* prefix (indexing time).

        The chatbot will encode queries with the *query* prefix at search time.
        This asymmetry is REQUIRED by the E5 model family for correct retrieval.
        """
        prefixed = [self.PASSAGE_PREFIX + t for t in texts]
        return self.embeddings_model.encode(
            prefixed,
            normalize_embeddings=self._normalize,
            show_progress_bar=True,
        )

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _content_id(text: str) -> str:
        """Stable, content-derived document ID — makes upsert idempotent."""
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def is_populated(self) -> bool:
        """True if the collection already has at least one document."""
        try:
            collection = self.client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": self._shared["hnsw_space"]},
            )
            return collection.count() > 0
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    #  Indexing (WRITE ONLY — no retrieval methods)                        #
    # ------------------------------------------------------------------ #

    def add_documents(self, documents: list, metadata: list = None):
        """
        Add documents to the vector store.

        Accepts:
          • LangChain Document objects (.page_content / .metadata)
          • File paths (str) — .docx or .pdf, with headers/footers stripped
        """
        if not documents:
            print("Warning: no documents supplied")
            return

        # ── Resolve file paths to LangChain Document objects ──────── #
        if isinstance(documents[0], str):
            processed: list = []
            for file_path in documents:
                if not os.path.isfile(file_path):
                    print(f"Warning: file not found — {file_path}")
                    continue
                ext = os.path.splitext(file_path)[1].lower()
                if ext in (".docx", ".pdf"):
                    chunks = self._loader.process_file(file_path)
                    processed.extend(chunks)
                else:
                    print(f"Warning: unsupported file type '{ext}' — {file_path}")

            if not processed:
                print("Warning: no valid documents were loaded from paths")
                return
            documents = processed

        # ── Upsert ────────────────────────────────────────────────── #
        collection = self.client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": self._shared["hnsw_space"]},
        )

        texts = [doc.page_content for doc in documents]
        embeddings = self.create_embeddings(texts)

        if metadata is None:
            metadata = [doc.metadata for doc in documents]

        # Content-hash IDs are stable across runs (idempotent upsert), but
        # identical chunks inside one batch would collide.  Suffix repeats
        # with an occurrence counter so every ID in the batch is unique.
        seen: dict[str, int] = {}
        ids: list[str] = []
        for t in texts:
            base = self._content_id(t)
            count = seen.get(base, 0)
            seen[base] = count + 1
            ids.append(base if count == 0 else f"{base}-{count}")

        collection.upsert(
            embeddings=embeddings.tolist(),
            documents=texts,
            metadatas=metadata,
            ids=ids,
        )
        print(f"Upserted {len(texts)} chunks into '{self._collection_name}'.")
