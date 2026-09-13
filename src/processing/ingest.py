"""
ingest.py
~~~~~~~~
Connects the crawler + extractor output to your existing ChromaDB pipeline.

Flow:
  1. Receive (text, metadata) pairs from the extractor
  2. Chunk with the configured strategy (fixed_size / semantic / header_based)
  3. Generate content-hashed IDs for deduplication
  4. Prefix with "passage: " (as required by multilingual-e5-large)
  5. Upsert into ChromaDB
"""

from __future__ import annotations

import hashlib
import time

from langchain_core.documents import Document as LCDocument

from src.core.vector_store import VectorStoreManager
from src.core.chunking import make_splitter
from src.processing.crawler import DomainCrawler, CrawledURL
from src.processing.extractor import ResourceExtractor
from src.processing.crawl_history import CrawlHistoryManager
from src.processing.context_generator import ContextGenerator


class IngestionPipeline:
    """
    Crawl -> Extract -> Chunk -> Embed -> Upsert pipeline.
    """

    def __init__(
        self,
        vector_store: VectorStoreManager,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        chunk_strategy: str = "fixed_size",
        max_depth: int = 3,
        delay: float = 1.0,
        timeout: int = 15,
        crawler_include_patterns: list[str] | None = None,
        crawler_exclude_patterns: list[str] | None = None,
        crawl_history: CrawlHistoryManager | None = None,
        context_generator: ContextGenerator | None = None,
    ):
        self.vector_store = vector_store
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.chunk_strategy = chunk_strategy
        self.max_depth = max_depth
        self.delay = delay
        self.timeout = timeout
        self.crawler_include_patterns = crawler_include_patterns
        self.crawler_exclude_patterns = crawler_exclude_patterns
        self.crawl_history = crawl_history
        self.context_generator = context_generator

        self._splitter = make_splitter(
            strategy=chunk_strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    # ------------------------------------------------------------------ #
    #  Ingest from a single base URL                                      #
    # ------------------------------------------------------------------ #

    def ingest_from_url(self, base_url: str) -> int:
        """
        Crawl a domain starting from base_url, extract text from every
        discovered resource, chunk, and upsert into the vector store.

        Returns the total number of chunks upserted.
        """
        total_chunks = 0

        # -- Step 1: Crawl --------------------------------------------- #
        print(f"\n=== Crawling: {base_url} ===")
        crawler = DomainCrawler(
            base_url=base_url,
            max_depth=self.max_depth,
            delay=self.delay,
            timeout=self.timeout,
            include_patterns=self.crawler_include_patterns,
            exclude_patterns=self.crawler_exclude_patterns,
        )
        results = crawler.crawl()

        extractable = [r for r in results if r.type in ("html", "pdf", "docx")]
        if not extractable:
            print("No extractable URLs found.")
            return 0

        # -- Filter out already-ingested URLs -------------------------- #
        skipped_already: list[CrawledURL] = []
        new_urls: list[CrawledURL] = []
        needs_enrichment: list[CrawledURL] = []
        if self.crawl_history:
            self.crawl_history.load()
            for r in extractable:
                if self.crawl_history.is_ingested(r.url):
                    skipped_already.append(r)
                    entry = self.crawl_history.get_entry(r.url)
                    if self.context_generator and not (
                        entry.get("context") and entry.get("questions")
                    ):
                        needs_enrichment.append(r)
                else:
                    new_urls.append(r)
            if skipped_already:
                print(f"Skipping {len(skipped_already)} already-ingested URLs "
                      f"(use CRAWL_HISTORY_ENABLED=False to force re-crawl)")
        else:
            new_urls = extractable

        if not new_urls and not needs_enrichment:
            print("All URLs already ingested -- nothing to do.")
            return 0

        # -- Step 2: Extract + Chunk + Upsert -------------------------- #
        print(f"\n=== Extracting {len(new_urls)} resources ===")
        extractor = ResourceExtractor(timeout=self.timeout)

        for i, crawled in enumerate(new_urls, start=1):
            print(f"\n[{i}/{len(new_urls)}] {crawled.type.upper()} {crawled.url}")

            extracted = extractor.extract(crawled.url, crawled.type)
            if extracted is None:
                continue

            text, meta = extracted
            if not text.strip():
                print(f"  No text extracted -- skipping")
                continue

            # Chunk the extracted text with the configured strategy
            chunks = self._splitter.create_documents(
                [text], metadatas=[meta]
            )
            print(f"  -> {len(chunks)} chunks  (strategy: {self.chunk_strategy})")

            # Upsert into ChromaDB
            if chunks:
                self._upsert_chunks(chunks)
                total_chunks += len(chunks)

            # Generate context + questions for the history entry
            context, questions = self._generate_context(crawled.url, text, meta)

            # Mark as ingested so we skip it next run
            if self.crawl_history:
                self.crawl_history.mark_ingested(
                    crawled.url,
                    crawled.type,
                    num_chunks=len(chunks) if chunks else 0,
                    context=context,
                    questions=questions,
                )

            # Politeness between extraction requests too
            if self.delay > 0 and i < len(new_urls):
                time.sleep(self.delay)

        # -- Step 3: Backfill context/questions for older entries ------ #
        if needs_enrichment:
            print(f"\n=== Generating context/questions for "
                  f"{len(needs_enrichment)} existing URLs ===")
            for i, crawled in enumerate(needs_enrichment, start=1):
                print(f"\n[{i}/{len(needs_enrichment)}] "
                      f"{crawled.type.upper()} {crawled.url}")

                extracted = extractor.extract(crawled.url, crawled.type)
                if extracted is None:
                    continue

                text, meta = extracted
                if not text.strip():
                    continue

                context, questions = self._generate_context(
                    crawled.url, text, meta
                )
                if context or questions:
                    self.crawl_history.set_context(
                        crawled.url, context, questions
                    )
                    print("  -> context/questions saved to crawl history")

                if self.delay > 0 and i < len(needs_enrichment):
                    time.sleep(self.delay)

        # -- Save crawl history ---------------------------------------- #
        if self.crawl_history:
            self.crawl_history.save()

        print(f"\n=== Done: {total_chunks} total chunks upserted ===")
        return total_chunks

    # ------------------------------------------------------------------ #
    #  Ingest from a list of pre-crawled URLs                             #
    # ------------------------------------------------------------------ #

    def ingest_from_url_list(self, crawled_urls: list[CrawledURL]) -> int:
        """
        Use this when you've already crawled and want to re-ingest,
        or when you have a hand-picked list of URLs.

        Parameters
        ----------
        crawled_urls : list[CrawledURL]
            Each entry must have .url and .type populated.

        Returns
        -------
        int -- total chunks upserted.
        """
        total_chunks = 0
        extractor = ResourceExtractor(timeout=self.timeout)

        for i, crawled in enumerate(crawled_urls, start=1):
            if crawled.type == "skip":
                continue

            print(f"[{i}/{len(crawled_urls)}] {crawled.type.upper()} {crawled.url}")
            extracted = extractor.extract(crawled.url, crawled.type)
            if extracted is None:
                continue

            text, meta = extracted
            if not text.strip():
                continue

            chunks = self._splitter.create_documents([text], metadatas=[meta])
            if chunks:
                self._upsert_chunks(chunks)
                total_chunks += len(chunks)

            if self.delay > 0:
                time.sleep(self.delay)

        return total_chunks

    # ------------------------------------------------------------------ #
    #  Upsert helper (reuses VectorStoreManager pattern)                  #
    # ------------------------------------------------------------------ #

    def _upsert_chunks(self, chunks: list[LCDocument]) -> None:
        """Add chunks to the vector store using the existing add_documents API."""
        self.vector_store.add_documents(chunks)

    # ------------------------------------------------------------------ #
    #  Context/question generation helper                                 #
    # ------------------------------------------------------------------ #

    def _generate_context(
        self, url: str, text: str, meta: dict
    ) -> tuple[str | None, list[str] | None]:
        """Generate a short context summary and questions for a page.

        Returns (None, None) when generation is disabled, the page has no
        text, or Ollama is unreachable — ingestion is never blocked.
        """
        if self.context_generator is None or not text.strip():
            return None, None

        print("  -> generating context/questions with Ollama ...")
        result = self.context_generator.generate(
            url, text, title=meta.get("filename")
        )
        if not result:
            return None, None
        return result.get("context"), result.get("questions")
