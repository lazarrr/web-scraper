"""
config/settings.py -- Scraper-only configuration.

All values can be overridden via environment variables or a .env file.
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ------------------------------------------------------------------ #
    #  Vector DB (shared with university-chatbot)                          #
    # ------------------------------------------------------------------ #
    VECTOR_DB_PATH: str = "./data/vector_store"

    # ------------------------------------------------------------------ #
    #  Chunking                                                            #
    # ------------------------------------------------------------------ #
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 200

    # ------------------------------------------------------------------ #
    #  Crawler                                                             #
    # ------------------------------------------------------------------ #
    CRAWLER_MAX_DEPTH: int = 1
    CRAWLER_DELAY: float = 1.0
    CRAWLER_TIMEOUT: int = 15
    CRAWLER_USER_AGENT: str = "Mozilla/5.0 (compatible; UniversityRAGBot/1.0)"

    # ------------------------------------------------------------------ #
    #  Crawl history                                                       #
    # ------------------------------------------------------------------ #
    CRAWL_HISTORY_ENABLED: bool = True
    CRAWL_HISTORY_PATH: str = "./data/crawl_history.json"

    # ------------------------------------------------------------------ #
    #  URL filtering                                                       #
    # ------------------------------------------------------------------ #
    CRAWLER_FILTER_MODE: str = "include"

    CRAWLER_INCLUDE_PATTERNS: list[str] = [
        r"^/$",
        r"^/studijski-programi",
        r"^/matematika-studije",
        r"^/informatika-studije",
        r"^/oglasna-tabla",
        r"",
    ]

    CRAWLER_EXCLUDE_PATTERNS: list[str] = [
        r"blog\.imi\.pmf\.kg\.ac\.rs",
        r"/images/",
        r"/office365",
    ]

    # ------------------------------------------------------------------ #
    #  Embedding model (read from shared_config.json at runtime;          #
    #  these are fallback defaults only)                                   #
    # ------------------------------------------------------------------ #
    EMBEDDING_MODEL: str = "intfloat/multilingual-e5-large"
    EMBEDDING_DEVICE: str = "cpu"

    class Config:
        env_file = ".env"


settings = Settings()
