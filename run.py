"""
run.py -- Entry point for the university-scraper.

Usage:
    python run.py

This script:
  1. Loads shared_config.json from the persist directory (or creates it
     with hard-coded defaults).
  2. Crawls https://imi.pmf.kg.ac.rs/ for HTML, PDF, and DOCX resources.
  3. Extracts clean text from each resource.
  4. Chunks, embeds (with E5 "passage: " prefix), and upserts into ChromaDB.
  5. Records which URLs have been ingested in crawl_history.json, so
     subsequent runs skip already-processed pages.

The resulting ChromaDB store is then ready to be read by the
university-chatbot project (which opens the same directory read-only).
"""

import logging
import json
import os

from src.core.vector_store import VectorStoreManager
from src.processing.ingest import IngestionPipeline
from src.processing.crawl_history import CrawlHistoryManager
from config.settings import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _ensure_shared_config(persist_dir: str) -> None:
    """
    If shared_config.json does not exist in the persist directory,
    write it out using the VectorStoreManager's hard-coded defaults.
    This guarantees that the chatbot project can discover the same
    constants later.
    """
    path = os.path.join(persist_dir, "shared_config.json")
    if os.path.exists(path):
        logger.info("shared_config.json already exists at %s", path)
        return

    defaults = VectorStoreManager._DEFAULTS
    os.makedirs(persist_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(defaults, fh, ensure_ascii=False, indent=2)
    logger.info("Wrote shared_config.json to %s", path)


def main() -> None:
    logger.info("=== University Scraper ===")

    # ── Ensure shared config exists before anything else ───────────── #
    _ensure_shared_config(settings.VECTOR_DB_PATH)

    # ── Initialise vector store (write-only) ───────────────────────── #
    vector_store = VectorStoreManager(settings)
    
    logger.info("==============================")
    logger.info(
        "Vector store — collection='%s', persist='%s' (absolute: %s)",
        vector_store._collection_name,
        settings.VECTOR_DB_PATH,
        os.path.abspath(settings.VECTOR_DB_PATH),
    )
    logger.info("==============================")


    # ── Crawl history ──────────────────────────────────────────────── #
    crawl_history: CrawlHistoryManager | None = None
    if settings.CRAWL_HISTORY_ENABLED:
        crawl_history = CrawlHistoryManager(
            file_path=settings.CRAWL_HISTORY_PATH,
            base_url="https://imi.pmf.kg.ac.rs/",
        )
        crawl_history.load()
        logger.info(
            "Crawl history loaded: %d URLs already ingested",
            crawl_history.ingested_count(),
        )

    # ── Build and run the pipeline ─────────────────────────────────── #
    pipeline = IngestionPipeline(
        vector_store=vector_store,
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
        chunk_strategy=settings.CHUNK_STRATEGY,
        max_depth=settings.CRAWLER_MAX_DEPTH,
        delay=settings.CRAWLER_DELAY,
        timeout=settings.CRAWLER_TIMEOUT,
        crawler_include_patterns=(
            settings.CRAWLER_INCLUDE_PATTERNS
            if settings.CRAWLER_FILTER_MODE == "include"
            else None
        ),
        crawler_exclude_patterns=(
            settings.CRAWLER_EXCLUDE_PATTERNS
            if settings.CRAWLER_FILTER_MODE == "exclude"
            else None
        ),
        crawl_history=crawl_history,
    )

    total = pipeline.ingest_from_url("https://imi.pmf.kg.ac.rs/")
    logger.info("=== Done: %d total chunks upserted ===", total)


if __name__ == "__main__":
    main()
