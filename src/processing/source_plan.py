"""
source_plan.py
~~~~~~~~~~~~~~
Builds the concrete scrape plan from data/urls.json.

urls.json is the single source of truth.  It contains:

  • HTML seeds           — concrete pages (imi.pmf.kg.ac.rs, www.pmf.kg.ac.rs, ...)
  • html_template seeds  — staff profiles ({staff_id}), Moodle catalogue ({n})
  • pdf_seeds            — curated PDFs grouped by topic (konkurs, cenovnik,
                           pravilnici, past entrance exams, prep lessons, ...)
  • course_syllabi       — 156 course-syllabus PDFs generated from slug lists
                           (directory listings return 403, so the URLs are
                           built from base + slug + extension)

This module expands all of that into a flat list of SeedSpec objects
consumed by the crawler / extractor / ingest pipeline, and carries the
crawl rules (normalisation, denylist, change detection) along with it.
"""

from __future__ import annotations

import json
import os
import re
import hashlib
from dataclasses import dataclass, field
from urllib.parse import urlparse as _urlparse

from src.processing.url_rules import DEFAULT_ALLOWED_PARAMS, normalize_url

# refresh cadence strings from urls.json -> seconds
REFRESH_SECONDS: dict[str, int] = {
    "daily": 24 * 3600,
    "weekly": 7 * 24 * 3600,
    "monthly": 30 * 24 * 3600,
    "quarterly": 91 * 24 * 3600,
    "yearly": 365 * 24 * 3600,
}
DEFAULT_REFRESH: int = REFRESH_SECONDS["monthly"]

# PDF-seed categories that are intentionally NOT seeded directly.
# Their paths change on every re-upload — the parent HTML pages
# (rasporedi / raspored-ispita) carry follow_pdfs=true and are the
# real discovery source.
VOLATILE_PDF_CATEGORIES: frozenset[str] = frozenset({"schedules_current"})

_PROGRAM_SLUGS: dict[str, str] = {
    "OAS Matematika": "oas-mat",
    "OAS Informatika": "oas-inf",
    "MAS Informatika": "mas-inf",
}


@dataclass
class SeedSpec:
    """One concrete URL to fetch."""

    id: str
    url: str
    title: str = ""
    category: str = ""
    priority: int = 1
    refresh: str = "monthly"
    type: str = "html"              # html | html_app | html_table | html_list
    #                                  | html_template | html_partial | pdf | docx
    follow_pdfs: bool = True
    notes: str = ""
    allowed_params: tuple[str, ...] = DEFAULT_ALLOWED_PARAMS
    max_depth: int = 1
    extract_only: str | None = None     # html_partial: keyword of the block
    strip_blocks: list[str] = field(default_factory=list)  # staff profiles
    follow_pattern: str | None = None   # regex a link must match to be followed

    @property
    def refresh_seconds(self) -> int:
        return REFRESH_SECONDS.get(self.refresh.strip().lower(), DEFAULT_REFRESH)

    @property
    def is_file(self) -> bool:
        return self.type in ("pdf", "docx")


@dataclass
class ScrapePlan:
    """Everything the pipeline needs, expanded from urls.json."""

    path: str
    meta: dict
    rules: dict
    denylist: list[str]
    content_extraction: list[dict]
    seeds: list[SeedSpec]
    id_walk: dict
    known_conflicts: list[dict]
    gaps: dict

    def __post_init__(self) -> None:
        # Exact seed-URL index so discovered links inherit the correct
        # seed metadata / refresh cadence (e.g. /vesti found via the nav
        # must refresh daily, not yearly).
        self._seed_index: dict[str, SeedSpec] = {}
        for seed in self.seeds:
            self._seed_index[normalize_url(seed.url, strip_query=False)] = seed

    # ------------------------------------------------------------------ #
    #  Seed lookup                                                         #
    # ------------------------------------------------------------------ #

    def lookup_seed(self, url: str) -> SeedSpec | None:
        """
        Return the plan seed this URL belongs to, if any.

        Exact seed URLs match first.  Article pages (/vesti/{id}-...)
        additionally resolve to the notice-board seeds, whose refresh
        cadence and category they inherit.
        """
        key = normalize_url(url, strip_query=False)
        seed = self._seed_index.get(key)
        if seed is not None:
            return seed

        host = _urlparse(key).netloc.lower()
        path = _urlparse(key).path
        if host in ("imi.pmf.kg.ac.rs",) and re.match(r"^/vesti/\d+", path):
            return self._seed_index.get(normalize_url("https://imi.pmf.kg.ac.rs/vesti", strip_query=False))
        if host in ("www.pmf.kg.ac.rs",) and path.startswith("/vesti/"):
            return self._seed_index.get(normalize_url("https://www.pmf.kg.ac.rs/index.php/vesti", strip_query=False))
        return None

    # ------------------------------------------------------------------ #
    #  Convenience accessors                                               #
    # ------------------------------------------------------------------ #

    @property
    def politeness_delay(self) -> float:
        ms = int(self.rules.get("politeness_delay_ms", 1500))
        return max(0.0, ms / 1000.0)

    @property
    def user_agent(self) -> str:
        return self.rules.get(
            "user_agent",
            "Mozilla/5.0 (compatible; UniversityRAGBot/1.0)",
        )

    @property
    def respect_robots(self) -> bool:
        return bool(self.rules.get("respect_robots_txt", True))

    @property
    def default_max_depth(self) -> int:
        return int(self.rules.get("max_depth_from_seed", 1))

    def seeds_by_priority(self, priorities: list[int]) -> list[SeedSpec]:
        allowed = set(priorities)
        return sorted(
            (s for s in self.seeds if s.priority in allowed),
            key=lambda s: (s.priority, s.category, s.id),
        )

    # ------------------------------------------------------------------ #
    #  Loading                                                             #
    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls, path: str) -> "ScrapePlan":
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        rules = data.get("crawl_rules", {})
        default_depth = int(rules.get("max_depth_from_seed", 1))

        plan = cls(
            path=os.path.abspath(path),
            meta=data.get("meta", {}),
            rules=rules,
            denylist=[d["pattern"] for d in rules.get("denylist", [])],
            content_extraction=rules.get("content_extraction", []),
            seeds=_expand_seeds(data, default_depth),
            id_walk=data.get("change_detection", {}) or {},
            known_conflicts=data.get("known_conflicts", []),
            gaps=data.get("gaps", {}),
        )
        return plan


# --------------------------------------------------------------------------- #
#  Seed expansion                                                             #
# --------------------------------------------------------------------------- #


def _expand_seeds(data: dict, default_depth: int) -> list[SeedSpec]:
    seeds: list[SeedSpec] = []
    for s in data.get("seeds", []):
        seeds.extend(_expand_html_seed(s, default_depth))
    seeds.extend(_expand_pdf_seeds(data.get("pdf_seeds", {})))
    seeds.extend(_expand_syllabi(data.get("course_syllabi", {})))
    return seeds


def _expand_html_seed(s: dict, default_depth: int) -> list[SeedSpec]:
    seed_type = s.get("type", "html")
    url = s.get("url", "")
    base = dict(
        id=s.get("id", ""),
        title=s.get("title_sr", s.get("title", "")),
        category=s.get("category", ""),
        priority=int(s.get("priority", 1)),
        refresh=s.get("refresh", "monthly"),
        type="html" if seed_type == "html_template" else seed_type,
        follow_pdfs=bool(s.get("follow_pdfs", True)),
        notes=s.get("notes", ""),
        max_depth=default_depth,
        extract_only=s.get("extract_only"),
        strip_blocks=s.get("strip_blocks", []),
    )

    if seed_type == "html_template":
        if "{staff_id}" in url:
            out = []
            for staff_id in s.get("ids", []):
                overrides = {"id": f"{s['id']}-{staff_id}",
                             "url": url.replace("{staff_id}", str(staff_id))}
                out.append(SeedSpec(**{**base, **overrides}))
            return out
        if "{n}" in url:
            out = []
            for n in s.get("allowed_category_ids", []):
                overrides = {
                    "id": f"{s['id']}-{n}",
                    "url": url.replace("{n}", str(n)),
                    "allowed_params": ("categoryid",),
                    "max_depth": 2,
                    "follow_pattern": r"/moodle/course/index\.php",
                }
                out.append(SeedSpec(**{**base, **overrides}))
            return out
        if "{slug}" in url:
            print(
                f"  [plan] SKIP seed '{s['id']}': no slugs supplied for "
                f"the {url!r} template (add slugs to urls.json to enable)"
            )
            return []
        print(f"  [plan] SKIP seed '{s['id']}': unknown template {url!r}")
        return []

    if not url:
        print(f"  [plan] SKIP seed '{s.get('id')}': missing URL")
        return []
    return [SeedSpec(**base, url=url)]


def _expand_pdf_seeds(pdf: dict) -> list[SeedSpec]:
    out: list[SeedSpec] = []
    for category, entries in pdf.items():
        if category in VOLATILE_PDF_CATEGORIES:
            print(
                f"  [plan] SKIP pdf_seeds.{category}: paths change on every "
                f"re-upload — resolved from parent HTML pages instead"
            )
            continue

        if isinstance(entries, dict) and "base" in entries:
            # prep_lessons_2026-style: base + list of files
            base = entries["base"]
            priority = int(entries.get("priority", 2))
            for fname in entries.get("files", []):
                url = base.rstrip("/") + "/" + fname
                out.append(_file_seed(
                    url, category=category, priority=priority,
                    title=fname, refresh="yearly",
                ))
            continue

        if not isinstance(entries, list):
            continue  # notes / metadata keys

        for e in entries:
            if not isinstance(e, dict) or not e.get("url"):
                continue
            out.append(_file_seed(
                e["url"],
                category=category,
                priority=int(e.get("priority", 1)),
                title=e.get("title", ""),
                refresh="yearly",
            ))
    return out


def _expand_syllabi(syllabi: dict) -> list[SeedSpec]:
    out: list[SeedSpec] = []
    for st in syllabi.get("sets", []):
        program = st.get("program", "unknown")
        prog_slug = _PROGRAM_SLUGS.get(program, re.sub(r"[^a-z0-9]+", "-", program.lower()))
        base = st.get("base", "").rstrip("/")
        ext = st.get("extension", ".pdf")
        priority = int(st.get("priority", syllabi.get("priority", 1)))
        for slug in st.get("slugs", []):
            url = f"{base}/{slug}{ext}"
            # Key the seed id on the FULL PATH, never the basename —
            # several basenames repeat across directories with different
            # content (zavrsni_rad, strucna_praksa, numericka_matematika...)
            out.append(_file_seed(
                url,
                category="syllabi",
                priority=priority,
                title=slug,
                refresh=syllabi.get("refresh", "yearly"),
                seed_id=f"syllabi-{prog_slug}-{slug}",
            ))
    return out


def _file_seed(url: str, category: str, priority: int, title: str,
               refresh: str, seed_id: str | None = None) -> SeedSpec:
    ext = os.path.splitext(_urlparse(url).path)[1].lower()
    file_type = "docx" if ext in (".docx", ".doc") else "pdf"
    return SeedSpec(
        id=seed_id or f"{category}-{hashlib.md5(url.encode('utf-8')).hexdigest()[:10]}",
        url=url,
        title=title,
        category=category,
        priority=priority,
        refresh=refresh,
        type=file_type,
        follow_pdfs=False,
    )
