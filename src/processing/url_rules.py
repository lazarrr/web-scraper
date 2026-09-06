"""
url_rules.py
~~~~~~~~~~~~
URL normalisation and denylist enforcement, driven entirely by
data/urls.json -> crawl_rules.

Why this exists
---------------
Neither target site ships a <link rel="canonical"> and arbitrary query
strings return HTTP 200 with identical content (e.g. /o-institutu?zzz=1
== /o-institutu).  Pagination params (?start, ?limitstart) are silent
no-ops.  Without rule-based normalisation the crawl frontier is
unbounded and the same page is ingested under 2-4 different URLs.

Rules implemented (in order, mirroring urls.json -> crawl_rules):
  1. lowercase_host
  2. strip_query_string_on_SEF_paths   (whitelist: id, page)
  3. strip_tab_selectors               (osnovne, master, doktorske, cesta-pitanja)
  4. collapse_index_php                (/?id=N  <->  /index.php?id=N)
  5. map_legacy_program_urls           (page=studije_* -> SEF alias)
  6. fix_missing_slash                 (host?id=N -> host/index.php?id=N)
  7. percent_encode_paths              (/pub/ filenames contain spaces,
                                       commas, parentheses, Cyrillic)
  8. canonical_form                    (prefer SEF, else /index.php?id=N)
"""

from __future__ import annotations

import re
from urllib.parse import (
    parse_qsl,
    parse_qs,
    quote,
    unquote,
    urlencode,
    urlparse,
    urlunparse,
)

# Params that are client-side JS tab selectors; the server returns the
# byte-identical full page for every value, so they are pure duplicates.
TAB_SELECTOR_PARAMS: frozenset[str] = frozenset(
    {"osnovne", "master", "doktorske", "cesta-pitanja"}
)

DEFAULT_ALLOWED_PARAMS: tuple[str, ...] = ("id", "page")

# The documented exception to the /moodle/** denylist: the course
# catalogue index pages only, and nothing below them.
MOODLE_CATALOG_PATH: str = "/moodle/course/index.php"

# Query params that are known no-ops / pure duplicates.
_NOOP_PARAMS: frozenset[str] = frozenset(
    {"limitstart", "start", "tmpl", "print", "sort", "ordering", "dir", "filter",
     "filter_order", "filter_order_Dir", "lang"}
)


# --------------------------------------------------------------------------- #
#  Normalisation                                                              #
# --------------------------------------------------------------------------- #


def normalize_url(
    url: str,
    allowed_params: tuple[str, ...] = DEFAULT_ALLOWED_PARAMS,
    strip_query: bool = True,
) -> str:
    """
    Return the canonical form of `url`.

    Parameters
    ----------
    url : str
        Absolute URL (caller is expected to urljoin() relative hrefs first).
    allowed_params : tuple[str, ...]
        Query params that survive normalisation.  Everything else is
        stripped because arbitrary params return HTTP 200 with identical
        content on both target sites.
    strip_query : bool
        False for curated seed URLs from urls.json — their params are
        known-good (e.g. kg.ac.rs/vest.php?vest=5330) and must not be
        stripped.  True for links discovered inside HTML pages.
    """
    url = url.strip()
    if not url:
        return url

    parts = urlparse(url)

    # ── lowercase_host ─────────────────────────────────────────────── #
    netloc = parts.netloc.lower()

    # ── fix_missing_slash ──────────────────────────────────────────── #
    # The site's own navigation emits "host?id=N" without a path.
    path = parts.path
    if not path and parts.query:
        path = "/index.php"

    # ── percent_encode_paths ───────────────────────────────────────── #
    # /pub/ filenames contain literal spaces, commas, parentheses and
    # Cyrillic; the site's own HTML is inconsistent about encoding them.
    # Unquote first so we never double-encode an already-encoded URL.
    path = quote(unquote(path), safe="/")

    # ── map_legacy_program_urls ────────────────────────────────────── #
    # /index.php?page=studije_mat&id=ANY  is byte-identical to the SEF
    # /matematika-studije page.  Map before any query stripping.
    q = parse_qs(parts.query, keep_blank_values=True)
    if "page" in q:
        page_val = q["page"][0] if q["page"] else ""
        if page_val.startswith("studije_mat"):
            return urlunparse((parts.scheme, netloc, "/matematika-studije",
                               "", "", ""))
        if page_val.startswith("studije_inf"):
            return urlunparse((parts.scheme, netloc, "/informatika-studije",
                               "", "", ""))

    # ── query handling ─────────────────────────────────────────────── #
    if strip_query:
        kept = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() in allowed_params and k.lower() not in TAB_SELECTOR_PARAMS
        ]
        # Drop known no-op params even if a seed whitelists them broadly.
        kept = [(k, v) for k, v in kept if k.lower() not in _NOOP_PARAMS]
        query = urlencode(kept)
        has_id = any(k.lower() == "id" for k, _ in kept)
    else:
        query = parts.query
        has_id = "id" in {k.lower() for k in q}

    # ── collapse_index_php + canonical_form ────────────────────────── #
    # SEF paths served under the index.php prefix (www.pmf.kg.ac.rs/
    # index.php/vesti) collapse to the plain SEF path.
    if path.startswith("/index.php/") and path != "/index.php/":
        path = path[len("/index.php"):]
    # id-based pages: the canonical form is /index.php?id=N
    if path == "/" and has_id:
        path = "/index.php"
    # bare /index.php (home) collapses to /
    if path == "/index.php" and not has_id:
        path = "/"

    return urlunparse((parts.scheme, netloc, path, "", query, ""))


# --------------------------------------------------------------------------- #
#  Denylist                                                                   #
# --------------------------------------------------------------------------- #


def _glob_to_regex(glob: str) -> str:
    """Convert a urls.json denylist glob ('*' wildcard) to a regex."""
    parts = []
    for chunk in re.split(r"(\*+)", glob):
        if not chunk:
            continue
        if chunk.startswith("*"):
            # '**' and '*' both mean "any characters"
            parts.append(".*")
        else:
            parts.append(re.escape(chunk))
    return "".join(parts)


def _expand_patterns(patterns: list[str]) -> list[str]:
    """
    Expand urls.json denylist entries into individual glob patterns.

    Several entries use '|' as an informal alternation:
        "www.pmf.kg.ac.rs/?id=705|443|1361|715|..."
        "match.pmf.kg.ac.rs/* | kjm.pmf.kg.ac.rs/* | www.pmf.kg.ac.rs/KJS/*"
    Bare fragments (like "443") inherit the host+param prefix of the
    first fragment so they don't match arbitrary URLs containing "443".
    """
    expanded: list[str] = []
    for entry in patterns:
        pieces = [p.strip() for p in entry.split("|") if p.strip()]
        if not pieces:
            continue
        if len(pieces) == 1:
            expanded.append(pieces[0])
            continue
        head = pieces[0]
        prefix = ""
        if "?id=" in head and head.count(".") >= 2:
            prefix = head.rsplit("=", 1)[0] + "="
        for piece in pieces:
            if prefix and not piece.startswith("/") and "." not in piece:
                expanded.append(prefix + piece)
            else:
                expanded.append(piece)
    return expanded


class Denylist:
    """Compiled denylist + host allowlist from urls.json -> crawl_rules."""

    MOODLE_EXCEPTION_HOSTS: frozenset[str] = frozenset(
        {"imi.pmf.kg.ac.rs", "www.imi.pmf.kg.ac.rs"}
    )

    def __init__(self, patterns: list[str]) -> None:
        self._regexes = [re.compile(_glob_to_regex(p), re.I)
                         for p in _expand_patterns(patterns)]

    # ------------------------------------------------------------------ #

    @staticmethod
    def _alternate_forms(url: str) -> set[str]:
        """The same page can be written /?id=N or /index.php?id=N."""
        return {url, url.replace("/index.php?", "/?"), url.replace("/?", "/index.php?")}

    def is_denied(self, url: str) -> bool:
        """True if `url` must not be fetched."""

        # ── Moodle: allow ONLY the course-catalogue index pages ─────── #
        # Documented exception in urls.json: "allow /moodle/course/
        # index.php?categoryid=N for the course catalogue only".
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host in self.MOODLE_EXCEPTION_HOSTS and "/moodle" in parsed.path:
            return parsed.path != MOODLE_CATALOG_PATH

        for candidate in self._alternate_forms(url):
            for rx in self._regexes:
                if rx.search(candidate):
                    return True
        return False


# --------------------------------------------------------------------------- #
#  Host allowlist                                                             #
# --------------------------------------------------------------------------- #


def build_host_allowlist(primary_host: str, secondary_hosts: list[str]) -> frozenset[str]:
    """Return the set of acceptable host names (www. handled both ways)."""
    allow: set[str] = set()
    for host in [primary_host, *secondary_hosts]:
        host = host.strip().lower().lstrip(".")
        if not host:
            continue
        allow.add(host)
        if host.startswith("www."):
            allow.add(host[4:])
        else:
            allow.add("www." + host)
    return frozenset(allow)


def is_host_allowed(url: str, allowlist: frozenset[str]) -> bool:
    """True if the URL's host is in the allowlist."""
    host = urlparse(url).netloc.lower()
    if not host:
        return False
    if host in allowlist:
        return True
    if host.startswith("www.") and host[4:] in allowlist:
        return True
    return "www." + host in allowlist
