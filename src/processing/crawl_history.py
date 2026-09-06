"""
crawl_history.py
~~~~~~~~~~~~~~~~
Persistent crawl history backed by a local JSON file.

Every URL that has been successfully fetched + ingested is recorded
with a timestamp, content type, chunk count, and the seed it came
from.  On subsequent runs each URL is compared against its seed's
refresh cadence (daily / weekly / monthly / ... from urls.json) and
skipped while still fresh.

The file also carries small pieces of pipeline state — currently the
last-seen article ID used by the monotonic notice-board walk.

Usage
-----
    history = CrawlHistoryManager("./data/crawl_history.json")
    history.load()

    if history.is_fresh(url, interval_seconds):
        print("Still fresh — skip")

    history.mark_ingested(url, url_type, num_chunks, seed_id)
    history.save()
"""

from __future__ import annotations

import json
import os
import time
from typing import Any


class CrawlHistoryManager:
    """
    Reads/writes a simple JSON crawl history.

    File format:
      {
        "version": 2,
        "urls": {
          "https://imi.pmf.kg.ac.rs/matematika-studije": {
            "type": "html",
            "chunks": 12,
            "seed_id": "imi-matematika",
            "ingested_at": 1712345678.123
          },
          ...
        },
        "state": {
          "last_article_id": 18845
        }
      }
    """

    def __init__(self, file_path: str):
        self.file_path = file_path
        self._data: dict[str, Any] = {"version": 2, "urls": {}, "state": {}}

    # ------------------------------------------------------------------ #
    #  I/O                                                                  #
    # ------------------------------------------------------------------ #

    def load(self) -> None:
        """Load history from disk.  No-op if the file doesn't exist."""
        if not os.path.exists(self.file_path):
            return
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except (json.JSONDecodeError, KeyError):
            # Corrupted file — start fresh
            self._data = {"version": 2, "urls": {}, "state": {}}
        self._data.setdefault("urls", {})
        self._data.setdefault("state", {})

    def save(self) -> None:
        """Persist the current history to disk."""
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------ #
    #  Query                                                                #
    # ------------------------------------------------------------------ #

    def is_ingested(self, url: str) -> bool:
        """Has this URL ever been ingested?"""
        return url in self._data.get("urls", {})

    def is_fresh(self, url: str, interval_seconds: int) -> bool:
        """
        True if `url` was ingested less than `interval_seconds` ago.
        A URL with no record is NOT fresh (it needs fetching).
        """
        entry = self._data.get("urls", {}).get(url)
        if entry is None:
            return False
        age = time.time() - float(entry.get("ingested_at", 0))
        return age < interval_seconds

    def ingested_count(self) -> int:
        """Total number of URLs in the history."""
        return len(self._data.get("urls", {}))

    def get_state(self, key: str, default: Any = None) -> Any:
        """Read a pipeline-state value (e.g. the last-seen article ID)."""
        return self._data.get("state", {}).get(key, default)

    # ------------------------------------------------------------------ #
    #  Mutate                                                               #
    # ------------------------------------------------------------------ #

    def mark_ingested(self, url: str, url_type: str, num_chunks: int = 0,
                      seed_id: str = "") -> None:
        """Record a URL as successfully ingested."""
        self._data.setdefault("urls", {})[url] = {
            "type": url_type,
            "chunks": num_chunks,
            "seed_id": seed_id,
            "ingested_at": time.time(),
        }

    def set_state(self, key: str, value: Any) -> None:
        """Write a pipeline-state value."""
        self._data.setdefault("state", {})[key] = value

    def clear(self) -> None:
        """Remove all history entries (forces a full re-crawl next run)."""
        self._data["urls"] = {}
