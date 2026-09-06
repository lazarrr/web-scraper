"""
crawler.py
~~~~~~~~~~
Seed-driven crawler for the urls.json scrape plan.

Unlike a classic BFS crawler it does NOT start from a homepage and
follow everything.  It starts from the concrete seed list expanded by
source_plan.py (HTML pages, staff profiles, Moodle catalogue pages) and
follows links only within the allowed hosts, up to a per-seed depth
limit.

Every discovered link passes through url_rules.normalize_url() and the
denylist BEFORE it is queued.  That is what keeps the frontier finite
on a site where arbitrary query strings return HTTP 200 with identical
content and where ?start= / ?limitstart= are silent no-ops.

Output: list[CrawledURL] — each with .url, .type, .depth, .seed_id.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from collections import deque

import requests
from bs4 import BeautifulSoup

from src.processing.source_plan import ScrapePlan, SeedSpec
from src.processing.url_rules import (
    Denylist,
    build_host_allowlist,
    is_host_allowed,
    normalize_url,
)

# URL schemes that are never crawled / queued.
_BLOCKED_SCHEMES: frozenset[str] = frozenset(
    {"mailto", "tel", "javascript", "data", "ftp", "file"}
)


@dataclass
class CrawledURL:
    url: str
    type: str      # "html" | "pdf" | "docx" | "skip"
    depth: int
    seed_id: str = ""


class SeedCrawler:
    """Crawls one seed at a time, honouring the urls.json crawl rules."""

    EXTRACTABLE_EXTENSIONS: set[str] = {".pdf", ".docx", ".doc"}
    SKIP_EXTENSIONS: set[str] = {
        ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
        ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
        ".mp4", ".mp3", ".avi", ".mov", ".wmv",
        ".css", ".js", ".ico", ".xml",
        ".xls", ".xlsx", ".ppt", ".pptx",
        ".exe", ".msi", ".dmg", ".pkg",
    }

    def __init__(
        self,
        plan: ScrapePlan,
        delay: float = 1.5,
        timeout: int = 15,
        user_agent: str | None = None,
    ):
        self.plan = plan
        self.delay = delay
        self.timeout = timeout

        self.denylist = Denylist(plan.denylist)
        self.robots_disallow = [
            p for p in plan.rules.get("robots_disallow", []) if p
        ]
        self.allowed_hosts = build_host_allowlist(
            plan.meta.get("primary_host", ""),
            plan.meta.get("secondary_hosts", []),
        )
        self.respect_robots = plan.respect_robots

        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent or plan.user_agent})

        self._robots: dict[str, RobotFileParser] = {}
        self._fetched: set[str] = set()   # URLs fetched for link discovery

    # ------------------------------------------------------------------ #
    #  Robots.txt                                                          #
    # ------------------------------------------------------------------ #

    def _is_allowed(self, url: str) -> bool:
        parsed = urlparse(url)

        # Static robots_disallow prefixes from urls.json
        for prefix in self.robots_disallow:
            if parsed.path.startswith(prefix):
                return False

        if not self.respect_robots:
            return True

        host = parsed.netloc.lower()
        if host not in self._robots:
            rp = RobotFileParser()
            rp.set_url(f"{parsed.scheme}://{host}/robots.txt")
            try:
                rp.read()
            except Exception:
                rp.allow_all = True
            self._robots[host] = rp

        ua = self._session.headers.get("User-Agent", "*")
        try:
            return self._robots[host].can_fetch(ua, url)
        except Exception:
            return True

    # ------------------------------------------------------------------ #
    #  URL classification                                                   #
    # ------------------------------------------------------------------ #

    @classmethod
    def _classify_url(cls, url: str) -> str:
        """Returns one of: "html", "pdf", "docx", "skip"."""
        path = urlparse(url).path.lower()

        for ext in cls.EXTRACTABLE_EXTENSIONS:
            if path.endswith(ext):
                return "pdf" if ext == ".pdf" else "docx"

        for ext in cls.SKIP_EXTENSIONS:
            if path.endswith(ext):
                return "skip"

        # No extension or .html/.php/.asp → treat as HTML
        if not re.search(r"\.\w{2,5}$", path):
            return "html"
        if re.search(r"\.(html?|php|asp|jsp|cfm)$", path):
            return "html"

        return "html"

    # ------------------------------------------------------------------ #
    #  Link extraction                                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_links(html: str, current_url: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        links: list[str] = []
        for tag in soup.find_all("a", href=True):
            href = tag["href"].strip()
            if not href or href.startswith("#"):
                continue
            absolute = urljoin(current_url, href)
            parts = urlparse(absolute)
            if parts.scheme.lower() in _BLOCKED_SCHEMES:
                continue
            links.append(absolute)
        return links

    # ------------------------------------------------------------------ #
    #  Crawl one seed                                                       #
    # ------------------------------------------------------------------ #

    def crawl_seed(self, seed: SeedSpec) -> list[CrawledURL]:
        """
        Crawl from a single seed URL.

        The seed URL itself is always reported (even at depth 0).  HTML
        seeds are fetched to discover further links up to the seed's
        depth limit; PDF/DOCX links are only queued when the seed says
        follow_pdfs.
        """
        results: list[CrawledURL] = []
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque()

        # The seed URL is curated — keep its query params verbatim.
        start = normalize_url(seed.url, allowed_params=seed.allowed_params,
                              strip_query=False)
        queue.append((start, 0))
        follow_re = re.compile(seed.follow_pattern) if seed.follow_pattern else None

        def enqueue(raw_link: str, depth: int) -> None:
            """Normalise + validate a discovered link before queueing."""
            normalized = normalize_url(
                raw_link, allowed_params=seed.allowed_params, strip_query=True
            )
            if normalized in visited:
                return
            if not is_host_allowed(normalized, self.allowed_hosts):
                return
            if self.denylist.is_denied(normalized):
                return
            link_type = self._classify_url(normalized)
            if link_type in ("pdf", "docx") and not seed.follow_pdfs:
                return
            if link_type == "skip":
                return
            if follow_re and not follow_re.search(normalized):
                return
            queue.append((normalized, depth))

        while queue:
            current_url, depth = queue.popleft()

            if current_url in visited:
                continue
            visited.add(current_url)

            if not self._is_allowed(current_url):
                print(f"  [robots.txt] Skipping: {current_url}")
                continue

            url_type = self._classify_url(current_url)
            results.append(CrawledURL(
                url=current_url, type=url_type, depth=depth, seed_id=seed.id
            ))

            if url_type != "html" or depth >= seed.max_depth:
                continue

            if current_url in self._fetched:
                continue
            self._fetched.add(current_url)

            try:
                response = self._session.get(current_url, timeout=self.timeout)
                response.raise_for_status()
            except Exception as exc:
                print(f"  [crawl-error] {current_url}: {exc}")
                continue

            for link in self._extract_links(response.text, current_url):
                enqueue(link, depth + 1)

            if self.delay > 0:
                time.sleep(self.delay)

        return results

    # ------------------------------------------------------------------ #

    @staticmethod
    def filter_by_type(results: list[CrawledURL], url_type: str) -> list[str]:
        """Return just the URLs of a given type."""
        return [r.url for r in results if r.type == url_type]
