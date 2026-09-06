"""
ingest.py
~~~~~~~~~
Connects the urls.json scrape plan to the ChromaDB pipeline.

Flow:
  1. Load data/urls.json (via source_plan.ScrapePlan)
  2. For every seed (sorted by priority): crawl / fetch, skipping URLs
     that are still fresh per their refresh cadence
  3. Extract clean text with ResourceExtractor
  4. Chunk with the configured strategy
  5. Expand every chunk into Cyrillic / Latin / ASCII variants
  6. Upsert into ChromaDB (content-hashed IDs make it idempotent)
  7. Run the monotonic article-ID walk for the notice board
"""

from __future__ import annotations

import logging
import re
import time

from bs4 import BeautifulSoup
from langchain_core.documents import Document as LCDocument

from src.core.vector_store import VectorStoreManager
from src.core.chunking import make_splitter
from src.processing.crawler import SeedCrawler, CrawledURL
from src.processing.extractor import ResourceExtractor, NOT_FOUND_MARKER
from src.processing.crawl_history import CrawlHistoryManager
from src.processing.source_plan import ScrapePlan, SeedSpec
from src.processing.transliteration import expand_variants

logger = logging.getLogger(__name__)

# Article IDs discovered in /oglasna-tabla or /vesti HTML.
_ARTICLE_HREF_RE = re.compile(r"/vesti/(\d+)")
_ARTICLE_MODAL_RE = re.compile(r"modal_(\d+)")
# DD.MM.YYYY and DD. MM. YYYY as displayed on the notice board.
_DATE_RE = re.compile(r"\b(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})\.?")


class IngestionPipeline:
    """
    Plan -> Crawl -> Extract -> Chunk -> (Cyr/Lat/ASCII variants) -> Upsert.
    """

    def __init__(
        self,
        vector_store: VectorStoreManager,
        plan: ScrapePlan,
        history: CrawlHistoryManager | None = None,
        chunk_size: int = 1000,
        chunk_overlap: int = 200,
        chunk_strategy: str = "fixed_size",
        delay: float = 1.5,
        timeout: int = 15,
        priorities: list[int] | None = None,
        force_refresh: bool = False,
        transliterate_variants: bool = True,
        id_walk_enabled: bool = True,
        id_walk_max_misses: int = 20,
        ocr_enabled: bool = False,
        user_agent: str | None = None,
    ):
        self.vector_store = vector_store
        self.plan = plan
        self.history = history
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.chunk_strategy = chunk_strategy
        self.delay = max(delay, plan.politeness_delay)
        self.timeout = timeout
        self.priorities = priorities or [1, 2, 3]
        self.force_refresh = force_refresh
        self.transliterate_variants = transliterate_variants
        self.id_walk_enabled = id_walk_enabled
        self.id_walk_max_misses = id_walk_max_misses

        self.crawler = SeedCrawler(plan, delay=self.delay, timeout=timeout,
                                   user_agent=user_agent)
        self.extractor = ResourceExtractor(
            plan, timeout=timeout, ocr_enabled=ocr_enabled,
            user_agent=user_agent,
        )

        self._splitter = make_splitter(
            strategy=chunk_strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    # ------------------------------------------------------------------ #
    #  Main entry point                                                    #
    # ------------------------------------------------------------------ #

    def ingest(self) -> int:
        """Ingest every seed in the plan. Returns the total chunks upserted."""
        seeds = self.plan.seeds_by_priority(self.priorities)
        total_chunks = 0

        logger.info("=== urls.json plan: %d seeds (priorities %s) ===",
                    len(seeds), self.priorities)

        for i, seed in enumerate(seeds, start=1):
            print(f"\n[{i}/{len(seeds)}] {seed.type.upper():>4s}  "
                  f"[p{seed.priority}] {seed.id}\n         {seed.url}")
            try:
                total_chunks += self._ingest_seed(seed)
            except Exception as exc:
                logger.warning("Seed %s failed: %s", seed.id, exc)

        if self.id_walk_enabled and self.plan.id_walk:
            total_chunks += self.run_id_walk()

        if self.history:
            self.history.save()

        logger.info("=== Done: %d total chunks upserted ===", total_chunks)
        return total_chunks

    # ------------------------------------------------------------------ #
    #  One seed                                                            #
    # ------------------------------------------------------------------ #

    def _ingest_seed(self, seed: SeedSpec) -> int:
        if seed.is_file:
            discovered = [CrawledURL(url=seed.url, type=seed.type,
                                     depth=0, seed_id=seed.id)]
        else:
            discovered = self.crawler.crawl_seed(seed)

        total = 0

        for crawled in discovered:
            # Discovered links inherit their OWN seed's metadata and
            # refresh cadence (a /vesti article found via the nav is a
            # daily notice, not part of the yearly page that linked it).
            attrib = self.plan.lookup_seed(crawled.url) or seed
            interval = attrib.refresh_seconds

            if self.history and not self.force_refresh \
                    and self.history.is_fresh(crawled.url, interval):
                print(f"  [fresh] skipping {crawled.url} "
                      f"(refresh: {attrib.refresh})")
                continue

            extracted = self.extractor.extract(crawled, attrib)
            if extracted is None:
                continue

            text, meta = extracted
            if not text.strip():
                print(f"  [empty] {crawled.url}")
                continue

            chunks = self._chunk_variants(text, meta)
            if not chunks:
                continue

            self.vector_store.add_documents(chunks)
            total += len(chunks)

            if self.history:
                self.history.mark_ingested(
                    crawled.url, crawled.type,
                    num_chunks=len(chunks), seed_id=attrib.id,
                )

            if self.delay > 0:
                time.sleep(self.delay)

        return total

    # ------------------------------------------------------------------ #
    #  Chunking + transliteration variants                                 #
    # ------------------------------------------------------------------ #

    def _chunk_variants(self, text: str, meta: dict) -> list[LCDocument]:
        """
        Chunk `text` once per surface form (original / other script /
        ASCII-folded) so Cyrillic, Latin and diacritic-less queries all
        retrieve the same facts.
        """
        if self.transliterate_variants:
            variants = expand_variants(text)
        else:
            variants = [(text, "original", _script_of(text))]

        chunks: list[LCDocument] = []
        for variant_text, variant_name, script in variants:
            variant_meta = {
                **meta,
                "variant": variant_name,
                "script": script,
            }
            chunked = self._splitter.create_documents(
                [variant_text], metadatas=[variant_meta]
            )
            chunks.extend(chunked)
        return chunks

    # ------------------------------------------------------------------ #
    #  Notice-board article-ID walk                                        #
    # ------------------------------------------------------------------ #

    def run_id_walk(self) -> int:
        """
        Incremental ingest of news/notice articles by monotonic ID.

        There is no RSS feed and no pagination; the notice board is a
        30-item rolling window with no archive.  But every article has a
        permanent page at /vesti/{ID}-<anything> where only the integer
        matters, IDs are never reused, and a 404 renders the literal
        string "Tražena stranica nije nađena.".  So walking IDs forward
        from the last-seen value ingests everything, nothing is lost,
        and it costs a handful of requests per run.
        """
        spec = self.plan.id_walk
        template = spec.get("url_template", "https://imi.pmf.kg.ac.rs/vesti/{id}-x")
        start = int(self.history.get_state("last_article_id",
                                           spec.get("last_seen_max_id_at_generation", 0)))
        notice_meta = self._harvest_notice_metadata(spec)

        article_seed = SeedSpec(
            id="imi-vesti-idwalk", url=template, title="Vesti (ID walk)",
            category="notices", priority=1, refresh="daily", type="html",
            follow_pdfs=False,
        )

        logger.info("=== Article-ID walk starting at %d ===", start + 1)

        misses = 0
        ingested = 0
        article_id = start + 1
        last_found = start
        while misses < self.id_walk_max_misses:
            url = template.replace("{id}", str(article_id))
            extracted = self.extractor.extract_article(url, article_seed)
            if extracted is None:
                misses += 1
            else:
                text, meta = extracted
                if NOT_FOUND_MARKER in text:
                    misses += 1
                else:
                    row = notice_meta.get(article_id)
                    if row:
                        meta["notice_date"] = row.get("date", "")
                        meta["notice_author"] = row.get("author", "")
                    chunks = self._chunk_variants(text, meta)
                    if chunks:
                        self.vector_store.add_documents(chunks)
                        ingested += len(chunks)
                    if self.history:
                        self.history.mark_ingested(
                            url, "html", num_chunks=len(chunks),
                            seed_id=article_seed.id,
                        )
                    misses = 0
                    last_found = article_id
                    print(f"  [id-walk] ingested {article_id}")
            article_id += 1
            if self.delay > 0:
                time.sleep(self.delay)

        if self.history:
            self.history.set_state("last_article_id", last_found)

        logger.info("=== Article-ID walk: %d new articles, %d chunks, "
                    "head now %d ===", last_found - start, ingested, last_found)
        return ingested

    def _harvest_notice_metadata(self, spec: dict) -> dict[int, dict[str, str]]:
        """
        Article pages carry NO publication date and NO author — that
        data exists only in the /oglasna-tabla table.  Fetch it once and
        map article IDs to (date, author).

        The notice-board titles expand via JS, but the row markup
        carries the article ID in data-reveal-id="modal_<ID>", so the
        mapping is exact.  Falls back to /vesti/{id} hrefs if the
        reveal attribute ever disappears.
        """
        result: dict[int, dict[str, str]] = {}
        board_url = spec.get("daily_supplement", {}).get("url")
        if not board_url:
            return result
        try:
            response = self.crawler._session.get(board_url, timeout=self.timeout)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
        except Exception as exc:
            logger.warning("Could not harvest /oglasna-tabla metadata: %s", exc)
            return result

        for tr in soup.find_all("tr"):
            reveal = tr.find("a", attrs={"data-reveal-id": True})
            if reveal is not None:
                m = _ARTICLE_MODAL_RE.search(reveal.get("data-reveal-id", ""))
                if not m:
                    continue
                row_id = int(m.group(1))
            else:
                href = tr.find("a", href=True)
                m = _ARTICLE_HREF_RE.search(href.get("href", "")) if href else None
                if not m:
                    continue
                row_id = int(m.group(1))

            date = ""
            author = ""
            for td in tr.find_all("td"):
                text = " ".join(td.get_text(" ", strip=True).split())
                if not text:
                    continue
                dm = _DATE_RE.search(text)
                if dm:
                    date = f"{dm.group(1)}.{dm.group(2)}.{dm.group(3)}."
                elif td.get("class") and "autor" in " ".join(td["class"]):
                    author = text
            result[row_id] = {"date": date, "author": author}

        if not result:
            logger.warning(
                "Could not map /oglasna-tabla rows to article IDs "
                "(JS-expanded rows) — ingesting articles without date/author"
            )
        return result


def _script_of(text: str) -> str:
    if re.search(r"[\u0400-\u04FF]", text):
        return "cyrillic"
    if re.search(r"[čćšžđČĆŠŽĐ]", text):
        return "latin"
    return "ascii"
