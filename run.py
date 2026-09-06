"""
run.py -- Entry point for the university-scraper (web_scraperV2).

Usage:
    python run.py

This script:
  1. Loads shared_config.json from the persist directory (or creates it
     with hard-coded defaults).
  2. Loads data/urls.json — the single source of truth for WHAT to
     scrape: HTML seeds, curated PDFs, the 156 course-syllabus PDFs,
     the denylist, URL-normalisation rules, and change detection.
  3. Crawls each seed (depth-limited, denylist-filtered, normalised).
  4. Extracts clean text, chunks, and indexes every chunk in up to
     three surface forms (original script, transliterated script,
     ASCII-folded).
  5. Embeds (E5 "passage: " prefix) and upserts into ChromaDB.
  6. Records per-URL history with refresh-cadence awareness so
     subsequent runs only re-fetch stale pages.
  7. Runs the monotonic article-ID walk for the notice board.

The resulting ChromaDB store is then ready to be read by the
university-chatbot project (which opens the same directory read-only).
"""

import logging
import json
import os

from src.core.vector_store import VectorStoreManager
from src.processing.ingest import IngestionPipeline
from src.processing.crawl_history import CrawlHistoryManager
from src.processing.source_plan import ScrapePlan
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
    logger.info("=== University Scraper (web_scraperV2) ===")

    # ── Ensure shared config exists before anything else ───────────── #
    _ensure_shared_config(settings.VECTOR_DB_PATH)

    # ── Load the scrape plan from urls.json ────────────────────────── #
    plan = ScrapePlan.load(settings.URLS_JSON_PATH)
    logger.info(
        "Loaded %s: %d seeds (%d HTML pages, %d PDF/DOCX), "
        "%d denylist rules, politeness %0.1fs",
        settings.URLS_JSON_PATH,
        len(plan.seeds),
        sum(1 for s in plan.seeds if not s.is_file),
        sum(1 for s in plan.seeds if s.is_file),
        len(plan.denylist),
        plan.politeness_delay,
    )

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
        )
        crawl_history.load()
        logger.info(
            "Crawl history loaded: %d URLs already ingested, "
            "last article ID %s",
            crawl_history.ingested_count(),
            crawl_history.get_state("last_article_id", "-"),
        )

    # ── Build and run the pipeline ─────────────────────────────────── #
    pipeline = IngestionPipeline(
        vector_store=vector_store,
        plan=plan,
        history=crawl_history,
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
        chunk_strategy=settings.CHUNK_STRATEGY,
        delay=settings.CRAWLER_DELAY,
        timeout=settings.CRAWLER_TIMEOUT,
        priorities=settings.INGEST_PRIORITIES,
        force_refresh=settings.FORCE_REFRESH,
        transliterate_variants=settings.TRANSLITERATE_VARIANTS,
        id_walk_enabled=settings.ID_WALK_ENABLED,
        id_walk_max_misses=settings.ID_WALK_MAX_MISSES,
        ocr_enabled=settings.OCR_ENABLED,
        user_agent=settings.CRAWLER_USER_AGENT,
    )

    total = pipeline.ingest()
    logger.info("=== Done: %d total chunks upserted ===", total)


if __name__ == "__main__":
    main()
