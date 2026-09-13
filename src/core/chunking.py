"""
chunking.py — Pluggable chunking strategies for the university-scraper.

Strategies:
    "fixed_size"  — RecursiveCharacterTextSplitter (current default)
    "semantic"    — Sentence-boundary-aware grouping with controllable overlap
    "header_based" — Splits on HTML/Markdown headers, falls back to fixed_size

Usage:
    from src.core.chunking import make_splitter
    splitter = make_splitter("semantic", chunk_size=1000, chunk_overlap=200)
    chunks = splitter.create_documents([text], metadatas=[meta])
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Protocol

from langchain_core.documents import Document as LCDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter


# --------------------------------------------------------------------------- #
#  Sentence-split regex (handles Serbian Latin + English end punctuation)     #
# --------------------------------------------------------------------------- #

_SENTENCE_RE = re.compile(
    r"(?<=[.!?\u2026\u203C\u2047-\u2049])\s+",
    re.UNICODE,
)


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences using a unicode-aware regex.

    Python's ``re`` has no ``\\p{Lu}`` property escape, so sentence
    boundaries are confirmed by checking that the next character is
    uppercase with ``str.isupper()``.
    """
    parts: list[str] = []
    start = 0
    for match in _SENTENCE_RE.finditer(text):
        next_char = text[match.end() : match.end() + 1]
        if next_char and next_char.isupper():
            parts.append(text[start : match.start()])
            start = match.end()
    parts.append(text[start:])
    return [s.strip() for s in parts if s.strip()]


# --------------------------------------------------------------------------- #
#  Strategy protocol                                                          #
# --------------------------------------------------------------------------- #


class TextSplitterProto(Protocol):
    """Minimal interface that every chunking strategy must satisfy."""

    def create_documents(
        self, texts: Iterable[str], metadatas: list[dict] | None = None
    ) -> list[LCDocument]: ...


# ============================================================================ #
#  1. Fixed-size  (unchanged behaviour)                                        #
# ============================================================================ #


def _make_fixed_splitter(chunk_size: int, chunk_overlap: int) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", ". ", " ", ""],
    )


# ============================================================================ #
#  2. Semantic / sentence-aware chunker                                        #
# ============================================================================ #


class SemanticChunker:
    """Group sentences into chunks that stay under chunk_size, with an
    overlap measured in *sentences* rather than characters."""

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        self.chunk_size = max(chunk_size, 100)
        self.chunk_overlap = max(chunk_overlap, 0)
        # Convert character overlap to sentence overlap approximation
        # (assume ~100 chars per sentence)
        self._overlap_sentences = max(1, self.chunk_overlap // 100)

    def create_documents(
        self, texts: Iterable[str], metadatas: list[dict] | None = None
    ) -> list[LCDocument]:
        docs: list[LCDocument] = []
        meta_list = metadatas or [{} for _ in texts]

        for text, meta in zip(texts, meta_list):
            sentences = _split_sentences(text)
            if not sentences:
                continue

            chunks: list[str] = []
            i = 0
            while i < len(sentences):
                chunk_lines: list[str] = []
                current_len = 0
                j = i
                while j < len(sentences):
                    s = sentences[j]
                    if current_len > 0 and current_len + len(s) + 1 > self.chunk_size:
                        break
                    chunk_lines.append(s)
                    current_len += len(s) + (1 if current_len > 0 else 0)
                    j += 1

                chunks.append("\n".join(chunk_lines))

                if j >= len(sentences):
                    break

                # Slide start back by overlap sentences
                i = max(i + 1, j - self._overlap_sentences)

            for chunk_text in chunks:
                docs.append(LCDocument(page_content=chunk_text, metadata=dict(meta)))

        return docs


# ============================================================================ #
#  3. Header-based chunker                                                     #
# ============================================================================ #

_HEADER_RE = re.compile(
    r"^#{1,6}\s+.*$|^<h[1-6][^>]*>.*?</h[1-6]>$",
    re.MULTILINE | re.IGNORECASE,
)

# Regex to detect h1-h6 start tags in HTML
_HTML_HEADER_OPEN_RE = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.IGNORECASE)

# Regex for markdown headers
_MD_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


class HeaderBasedChunker:
    """Split text at header boundaries (HTML <h1>-<h6> or Markdown #). Each
    header plus its following content becomes a section.  Sections that
    still exceed chunk_size are further split with the fixed-size strategy."""

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._fallback = _make_fixed_splitter(chunk_size, chunk_overlap)

    def _split_on_headers(self, text: str) -> list[tuple[str, str]]:
        """Return list of (header_text, body_text) pairs."""
        text_stripped = text.strip()
        if not text_stripped:
            return []

        # Try HTML headers first
        html_matches = list(_HTML_HEADER_OPEN_RE.finditer(text_stripped))
        if html_matches:
            return self._split_html(text_stripped, html_matches)

        # Try Markdown headers
        md_matches = list(_MD_HEADER_RE.finditer(text_stripped))
        if md_matches:
            return self._split_markdown(text_stripped, md_matches)

        return [("", text_stripped)]

    def _split_html(self, text: str, matches) -> list[tuple[str, str]]:
        sections: list[tuple[str, str]] = []
        for idx, m in enumerate(matches):
            header = m.group(0)
            body_start = m.end()
            body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            body = text[body_start:body_end].strip()
            sections.append((header, body))
        return sections

    def _split_markdown(self, text: str, matches) -> list[tuple[str, str]]:
        sections: list[tuple[str, str]] = []
        for idx, m in enumerate(matches):
            header = m.group(0)
            body_start = m.end()
            body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            body = text[body_start:body_end].strip()
            sections.append((header, body))
        return sections

    def create_documents(
        self, texts: Iterable[str], metadatas: list[dict] | None = None
    ) -> list[LCDocument]:
        docs: list[LCDocument] = []
        meta_list = metadatas or [{} for _ in texts]

        for text, meta in zip(texts, meta_list):
            sections = self._split_on_headers(text)
            if not sections:
                continue

            for header, body in sections:
                full_text = f"{header}\n{body}" if header else body
                if len(full_text) <= self.chunk_size:
                    docs.append(LCDocument(page_content=full_text, metadata=dict(meta)))
                else:
                    sub_chunks = self._fallback.create_documents(
                        [full_text], metadatas=[dict(meta)]
                    )
                    docs.extend(sub_chunks)

        return docs


# ============================================================================ #
#  Factory                                                                     #
# ============================================================================ #


def make_splitter(
    strategy: str = "fixed_size",
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
) -> TextSplitterProto:
    """Return a text splitter that implements the chosen chunking strategy.

    Parameters
    ----------
    strategy : str
        One of "fixed_size", "semantic", "header_based".
    """
    strategy = strategy.lower().strip()
    if strategy == "semantic":
        return SemanticChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    elif strategy == "header_based":
        return HeaderBasedChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    else:
        return _make_fixed_splitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
