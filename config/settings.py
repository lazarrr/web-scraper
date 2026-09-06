"""
config/settings.py -- Scraper-only configuration.

All values can be overridden via environment variables or a .env file.

The scrape PLAN itself (which URLs, priorities, refresh cadences,
denylist, crawl rules) lives in data/urls.json — this module only
controls runtime behaviour.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ------------------------------------------------------------------ #
    #  Vector DB (shared with university-chatbot)                          #
    # ------------------------------------------------------------------ #
    VECTOR_DB_PATH: str = "./data/vector_store"

    # ------------------------------------------------------------------ #
    #  Scrape plan (data source)                                           #
    # ------------------------------------------------------------------ #
    URLS_JSON_PATH: str = "./data/urls.json"

    # Only seeds whose priority is in this list are ingested.
    INGEST_PRIORITIES: list[int] = [1, 2, 3]

    # ------------------------------------------------------------------ #
    #  Chunking                                                            #
    # ------------------------------------------------------------------ #
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 200
    CHUNK_STRATEGY: str = "fixed_size"

    # ------------------------------------------------------------------ #
    #  Crawler                                                             #
    # ------------------------------------------------------------------ #
    CRAWLER_DELAY: float = 1.5
    CRAWLER_TIMEOUT: int = 15
    CRAWLER_USER_AGENT: str | None = None  # None -> urls.json user_agent

    # ------------------------------------------------------------------ #
    #  Crawl history / refresh                                             #
    # ------------------------------------------------------------------ #
    CRAWL_HISTORY_ENABLED: bool = True
    CRAWL_HISTORY_PATH: str = "./data/crawl_history.json"
    FORCE_REFRESH: bool = False  # ignore refresh cadence, re-ingest everything

    # ------------------------------------------------------------------ #
    #  Notice-board article-ID walk                                        #
    # ------------------------------------------------------------------ #
    ID_WALK_ENABLED: bool = True
    ID_WALK_MAX_MISSES: int = 20

    # ------------------------------------------------------------------ #
    #  Text variants (dual script + ASCII folding)                         #
    # ------------------------------------------------------------------ #
    TRANSLITERATE_VARIANTS: bool = True

    # ------------------------------------------------------------------ #
    #  OCR for scanned PDFs (smanjenje4112025.pdf, ...)                    #
    # ------------------------------------------------------------------ #
    OCR_ENABLED: bool = False

    # ------------------------------------------------------------------ #
    #  Embedding model (read from shared_config.json at runtime;          #
    #  these are fallback defaults only)                                   #
    # ------------------------------------------------------------------ #
    EMBEDDING_MODEL: str = "intfloat/multilingual-e5-large"
    EMBEDDING_DEVICE: str = "cpu"

    # ------------------------------------------------------------------ #
    #  Hybrid search (dense + BM25 sparse)                                 #
    # ------------------------------------------------------------------ #
    HYBRID_SEARCH_ENABLED: bool = True
    HYBRID_CANDIDATE_COUNT: int = 20
    RRF_K: int = 60
    RRF_DENSE_WEIGHT: float = 0.5
    RRF_SPARSE_WEIGHT: float = 0.5

    # ------------------------------------------------------------------ #
    #  Cross-encoder reranking                                             #
    # ------------------------------------------------------------------ #
    RERANKER_ENABLED: bool = True
    RERANKER_MODEL: str = "BAAI/bge-reranker-v2-m3"
    RERANKER_DEVICE: str = "cpu"
    RERANKER_MAX_LENGTH: int = 512
    RERANKER_TOP_K: int = 5

    # ------------------------------------------------------------------ #
    #  Similarity threshold                                                #
    # ------------------------------------------------------------------ #
    SIMILARITY_THRESHOLD: float = 0.0

    class Config:
        env_file = ".env"


settings = Settings()
