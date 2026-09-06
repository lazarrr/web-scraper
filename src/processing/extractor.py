"""
extractor.py
~~~~~~~~~~~~
Extracts clean text from HTML / PDF / DOCX resources, honouring the
content-extraction rules in data/urls.json.

HTML   → BeautifulSoup; strips nav/header/footer/scripts; drops the
         boilerplate address block that repeats on ~42 staff pages;
         prefers the <h1> as title; handles html_table / html_app /
         html_partial / staff-profile seed types.
PDF    → pdfplumber (inline stream, then temp-file fallback).  Image
         scans with no text layer are detected and reported (OCR is
         optional via the ocr_enabled flag).
DOCX   → python-docx via a temp file.

Every returned metadata dict carries the seed provenance (seed_id,
category, priority, refresh) plus a best-effort academic year, so the
vector store can prefer the newest claim when years conflict.

Output: (text: str, metadata: dict) for each resource, or None.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Tag

from src.processing.source_plan import ScrapePlan, SeedSpec
from src.processing.crawler import CrawledURL

# Sitewide boilerplate containers on imi.pmf.kg.ac.rs that live outside
# the <nav>/<footer> tags (Foundation "metro tile" menu carousels etc.).
_BOILERPLATE_CLASSES: tuple[str, ...] = (
    "top-bar", "title-area", "off-canvas-menu", "owl-carousel",
    "thin_carousel",
)

# The staff-profile content container (holds only the contact card).
_STAFF_CONTAINER_ID: str = "container_nas_osoblje_sub"

# Article-page 404 signature (from urls.json -> change_detection).
NOT_FOUND_MARKER: str = "Tražena stranica nije nađena."

# Boilerplate that repeats on ~42 staff pages and in every footer.
# Kept only when the seed itself is a contact/location page.
_ADDRESS_PATTERNS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.I) for p in (
    r"Radoja Domanovi[cć]a",
    r"335\s?-?\s?040",
    r"^[\s-]*(fax|faks)[\s:.-]",
    r"^[\s-]*телефон",           # header/footer telephone block lines
    r"^[\s-]*Telefon",
))
_CONTACT_SEEDS: frozenset[str] = frozenset(
    {"imi-kontakt", "pmf-lokacija", "sckg-home"}
)

# Staff-profile section headings: everything from the first match on is
# CV/publications/conference material, not contact-card content.
_STAFF_STRIP_KEYWORDS: tuple[str, ...] = (
    "cv", "биографи", "biograf", "публикаци", "publikacije", "радов",
    "radovi", "конференци", "konferencije", "галери", "galerija",
    "пројекти", "projekti", "research", "publications", "conferences",
)

_YEAR_RE = re.compile(r"(20\d{2})(?:[/\-_](?:19)?(\d{2}))?")


def _academic_year(text: str) -> str:
    """Best-effort academic-year tag from a filename/URL (e.g. 2026/27)."""
    matches = _YEAR_RE.findall(text)
    if not matches:
        return ""
    start, end = matches[-1]
    if end:
        return f"{start}/{end}"
    return start


def _detect_academic_year(url: str, body: str) -> str:
    year = _academic_year(url)
    if year:
        return year
    # Try the beginning of the body where "2026/27" style headers live.
    head = body[:1500]
    year = _academic_year(head)
    return year


class ResourceExtractor:
    """Extract clean text from web resources by type."""

    def __init__(
        self,
        plan: ScrapePlan | None = None,
        timeout: int = 15,
        user_agent: str | None = None,
        ocr_enabled: bool = False,
    ):
        self.timeout = timeout
        self.ocr_enabled = ocr_enabled
        self.plan = plan
        self._session = requests.Session()
        ua = user_agent or (plan.user_agent if plan else None) or \
            "Mozilla/5.0 (compatible; UniversityRAGBot/1.0)"
        self._session.headers.update({"User-Agent": ua})

    # ------------------------------------------------------------------ #
    #  Fetch helper                                                         #
    # ------------------------------------------------------------------ #

    def _fetch(self, url: str, stream: bool = False):
        """GET a URL, returning a requests.Response or raising on failure."""
        response = self._session.get(url, timeout=self.timeout, stream=stream)
        response.raise_for_status()
        return response

    # ------------------------------------------------------------------ #
    #  Base metadata                                                        #
    # ------------------------------------------------------------------ #

    def _base_meta(self, url: str, filename: str, url_type: str,
                   seed: SeedSpec | None, body: str = "") -> dict:
        meta = {
            "source":   url,
            "filename": filename,
            "type":     "web" if url_type == "html" else url_type,
            "page":     0,
            "url":      url,
            "academic_year": _detect_academic_year(url, body) or "",
        }
        if seed is not None:
            meta.update({
                "seed_id":  seed.id,
                "category": seed.category,
                "priority": seed.priority,
                "refresh":  seed.refresh,
            })
        return meta

    # ------------------------------------------------------------------ #
    #  HTML extraction                                                      #
    # ------------------------------------------------------------------ #

    def _find_title(self, soup: BeautifulSoup, url: str) -> str:
        """Prefer the <h1> (rules: prefer_h1_as_title); fall back to <title>."""
        h1 = soup.find("h1")
        if h1:
            text = " ".join(h1.get_text(" ", strip=True).split())
            if text:
                return text
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        return url

    @staticmethod
    def _table_text(soup: BeautifulSoup) -> str:
        """Row-per-line text for html_table / html_app seeds."""
        lines: list[str] = []
        for table in soup.find_all("table"):
            for tr in table.find_all("tr"):
                cells = [
                    " ".join(td.get_text(" ", strip=True).split())
                    for td in tr.find_all(["td", "th"])
                    if td.get_text(strip=True)
                ]
                if cells:
                    lines.append(" | ".join(cells))
            lines.append("")
        return "\n".join(lines)

    def _strip_staff_blocks(self, body: Tag) -> None:
        """Cut everything from the first CV/publications heading on."""
        for heading in body.find_all(["h1", "h2", "h3", "h4"]):
            text = heading.get_text(" ", strip=True).lower()
            if any(kw in text for kw in _STAFF_STRIP_KEYWORDS):
                # Decompose this heading and every following sibling.
                node = heading
                while node is not None:
                    nxt = node.find_next_sibling()
                    node.decompose()
                    node = nxt
                return

    def _extract_partial(self, soup: BeautifulSoup, keyword: str) -> str:
        """Extract one topic block (html_partial seeds with extract_only)."""
        target = soup.find(
            lambda tag: isinstance(tag, Tag)
            and keyword.lower() in tag.get_text(" ", strip=True).lower()
        )
        if target is None:
            return ""
        lines: list[str] = []
        node: Tag | None = target
        while node is not None:
            if node.name in ("h1", "h2", "h3", "h4") and node is not target:
                break
            text = node.get_text(" ", strip=True)
            if text:
                lines.append(text)
            node = node.find_next_sibling()
        return "\n".join(lines)

    def _extract_html(self, url: str, seed: SeedSpec | None) -> tuple[str, dict]:
        """Extract clean text from an HTML page."""
        response = self._fetch(url)
        soup = BeautifulSoup(response.text, "html.parser")

        # Article-page 404 detection (server returns HTTP 200 shells).
        if NOT_FOUND_MARKER in soup.get_text():
            return "", {}

        # Remove non-content elements
        for tag in soup(["script", "style", "nav", "header", "footer",
                         "aside", "form", "noscript", "iframe"]):
            tag.decompose()
        # Remove Foundation boilerplate containers that are not inside
        # the tags above (menu tile carousels etc.)
        for cls in _BOILERPLATE_CLASSES:
            for tag in soup.find_all(class_=cls):
                tag.decompose()

        # Staff profiles: extract only the contact-card container.
        if seed is not None and seed.strip_blocks:
            staff_container = soup.find(id=_STAFF_CONTAINER_ID)
            if staff_container is not None:
                self._strip_staff_blocks(staff_container)
                body_tag = staff_container
            else:
                body_tag = soup.find("main") or soup.find("article") or soup.body
                if body_tag is not None:
                    self._strip_staff_blocks(body_tag)
        else:
            body_tag = soup.find("main") or soup.find("article") or soup.body
        if body_tag is None:
            return "", {}

        if seed is not None and seed.extract_only:
            partial = self._extract_partial(soup, seed.extract_only)
            if partial.strip():
                text = partial
            else:
                text = self._body_text(body_tag)
        else:
            text = self._body_text(body_tag)

        # The notice-board / timetable pages live almost entirely in tables.
        if seed is not None and seed.type in ("html_table", "html_app"):
            text = self._table_text(soup) + "\n" + text

        if seed is None or seed.id not in _CONTACT_SEEDS:
            text = self._strip_boilerplate(text)

        title = self._find_title(soup, url)
        meta = self._base_meta(url, title, "html", seed, body=text)
        return text, meta

    @staticmethod
    def _body_text(body: Tag) -> str:
        lines = [
            line.strip()
            for line in body.get_text(separator="\n").splitlines()
            if line.strip()
        ]
        return "\n".join(lines)

    @staticmethod
    def _strip_boilerplate(text: str) -> str:
        """Drop the repeating address/fax block (see urls.json rules)."""
        kept: list[str] = []
        for line in text.splitlines():
            if any(p.search(line) for p in _ADDRESS_PATTERNS):
                continue
            kept.append(line)
        return "\n".join(kept)

    def extract_article(self, url: str, seed: SeedSpec | None = None):
        """
        Extract an article page, returning None when it is a 404 shell.
        (Used by the monotonic article-ID walk.)
        """
        try:
            text, meta = self._extract_html(url, seed)
        except requests.HTTPError:
            return None
        except Exception as exc:
            print(f"  [article-error] {url}: {exc}")
            return None
        if not text.strip():
            return None
        return text, meta

    # ------------------------------------------------------------------ #
    #  PDF extraction                                                       #
    # ------------------------------------------------------------------ #

    def _extract_pdf(self, url: str, seed: SeedSpec | None) -> tuple[str, dict] | None:
        """Extract text from a PDF at the given URL."""
        filename = urlparse(url).path.rsplit("/", 1)[-1] or url

        # Attempt 1: stream into pdfplumber
        tmp_path: str | None = None
        try:
            response = self._fetch(url, stream=True)
            content = response.content

            # Course-syllabus gotcha: a nonexistent slug returns an EMPTY
            # HTML SHELL at HTTP 200, not a 404.  Check the magic bytes.
            if not content.startswith(b"%PDF"):
                print(f"  [pdf] Not a PDF (empty shell?): {url}")
                return None

            import pdfplumber
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                pages = [page.extract_text() or "" for page in pdf.pages]
            text = "\n\n".join(p for p in pages if p.strip())

            if not text.strip():
                return self._ocr_or_none(url, filename, seed, content)

            meta = self._base_meta(url, filename, "pdf", seed, body=text)
            return text, meta
        except Exception:
            pass  # fall through to the temp-file approach

        # Attempt 2: download to temp file (also used for OCR)
        try:
            response = self._fetch(url)
            if not response.content.startswith(b"%PDF"):
                print(f"  [pdf] Not a PDF (empty shell?): {url}")
                return None
            suffix = ".pdf"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name

            import pdfplumber
            with pdfplumber.open(tmp_path) as pdf:
                pages = [page.extract_text() or "" for page in pdf.pages]
            text = "\n\n".join(p for p in pages if p.strip())

            if not text.strip():
                return self._ocr_or_none(url, filename, seed,
                                         response.content, tmp_path)

            meta = self._base_meta(url, filename, "pdf", seed, body=text)
            return text, meta
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _ocr_or_none(self, url: str, filename: str, seed: SeedSpec | None,
                     content: bytes, tmp_path: str | None = None) -> tuple[str, dict] | None:
        """
        A PDF with no text layer is a scanned image.  Try OCR if enabled,
        otherwise report and skip (per urls.json: smanjenje4112025.pdf,
        saopstenje2026.pdf, kmatematika2026.pdf are known scans).
        """
        print(f"  [pdf] SCANNED PDF (no text layer): {url}")
        if not self.ocr_enabled:
            print(f"  [pdf]   OCR disabled — skipping. Re-run with OCR_ENABLED=true.")
            return None

        try:
            import ocrmypdf
        except ImportError:
            print(f"  [pdf]   ocrmypdf not installed — skipping.")
            return None

        work_path = tmp_path
        if work_path is None:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(content)
                work_path = tmp.name
        ocr_path = work_path + ".ocr.pdf"
        try:
            ocrmypdf.ocr(work_path, ocr_path, language=["srp"], deskew=True)
            import pdfplumber
            with pdfplumber.open(ocr_path) as pdf:
                pages = [page.extract_text() or "" for page in pdf.pages]
            text = "\n\n".join(p for p in pages if p.strip())
        except Exception as exc:
            print(f"  [pdf] OCR failed for {url}: {exc}")
            return None
        finally:
            for p in (work_path, ocr_path):
                if p and p != tmp_path and os.path.exists(p):
                    os.unlink(p)

        if not text.strip():
            return None
        meta = self._base_meta(url, filename, "pdf", seed, body=text)
        return text, meta

    # ------------------------------------------------------------------ #
    #  DOCX extraction                                                      #
    # ------------------------------------------------------------------ #

    def _extract_docx(self, url: str, seed: SeedSpec | None) -> tuple[str, dict] | None:
        """Extract text from a DOCX file. Always downloads to temp."""
        filename = urlparse(url).path.rsplit("/", 1)[-1] or url
        tmp_path: str | None = None

        try:
            response = self._fetch(url)
            suffix = os.path.splitext(filename)[1] or ".docx"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(response.content)
                tmp_path = tmp.name

            from docx import Document
            doc = Document(tmp_path)
            paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
            text = "\n\n".join(paragraphs)

            meta = self._base_meta(url, filename, "docx", seed, body=text)
            return text, meta
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    # ------------------------------------------------------------------ #
    #  Public API                                                           #
    # ------------------------------------------------------------------ #

    def extract(self, crawled: CrawledURL, seed: SeedSpec | None = None):
        """
        Extract clean text from a crawled URL.

        Parameters
        ----------
        crawled : CrawledURL
        seed : SeedSpec | None
            The seed this URL came from (for provenance metadata and
            per-seed extraction behaviour).

        Returns
        -------
        (text, metadata) or None if extraction fails.
        """
        url, url_type = crawled.url, crawled.type
        try:
            if url_type == "html":
                text, meta = self._extract_html(url, seed)
                if not text.strip():
                    return None
                return text, meta
            elif url_type == "pdf":
                return self._extract_pdf(url, seed)
            elif url_type == "docx":
                return self._extract_docx(url, seed)
            else:
                print(f"Unknown URL type '{url_type}' for {url}")
                return None
        except requests.HTTPError as exc:
            print(f"  [http-error] {url}: {exc}")
            return None
        except Exception as exc:
            print(f"  [extract-error] {url}: {exc}")
            return None
