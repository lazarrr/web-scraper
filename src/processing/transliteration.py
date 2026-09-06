"""
transliteration.py
~~~~~~~~~~~~~~~~~~
Serbian dual-script handling for the university-scraper.

The two target sites mix Serbian Cyrillic and Latin with no switcher —
sometimes on the same page.  "Нумеричка математика" / "Numerička
matematika" / "numericka_matematika" are three surface forms of one
entity, and students additionally type without diacritics.

Per urls.json -> crawl_rules.content_extraction, EVERY chunk is indexed
in up to three surface forms:

    original       — exactly as extracted from the page/PDF
    transliterated — the same text in the OTHER script (Cyrillic <-> Latin)
    ascii          — diacritics folded: č->c, š->s, ž->z, ć->c, đ->dj

The search/chatbot side should apply the same ascii fold to queries.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
#  Cyrillic -> Latin (Gajica)                                                  #
# --------------------------------------------------------------------------- #

_CYR_TO_LAT_PAIRS: list[tuple[str, str]] = [
    # digraphs first
    ("Љ", "Lj"), ("Њ", "Nj"), ("Џ", "Dž"), ("Ђ", "Đ"),
    ("љ", "lj"), ("њ", "nj"), ("џ", "dž"), ("ђ", "đ"),
    # uppercase
    ("А", "A"), ("Б", "B"), ("В", "V"), ("Г", "G"), ("Д", "D"),
    ("Е", "E"), ("Ж", "Ž"), ("З", "Z"), ("И", "I"), ("Ј", "J"),
    ("К", "K"), ("Л", "L"), ("М", "M"), ("Н", "N"), ("О", "O"),
    ("П", "P"), ("Р", "R"), ("С", "S"), ("Т", "T"), ("Ћ", "Ć"),
    ("У", "U"), ("Ф", "F"), ("Х", "H"), ("Ц", "C"), ("Ч", "Č"),
    ("Ш", "Š"),
    # lowercase
    ("а", "a"), ("б", "b"), ("в", "v"), ("г", "g"), ("д", "d"),
    ("е", "e"), ("ж", "ž"), ("з", "z"), ("и", "i"), ("ј", "j"),
    ("к", "k"), ("л", "l"), ("м", "m"), ("н", "n"), ("о", "o"),
    ("п", "p"), ("р", "r"), ("с", "s"), ("т", "t"), ("ћ", "ć"),
    ("у", "u"), ("ф", "f"), ("х", "h"), ("ц", "c"), ("ч", "č"),
    ("ш", "š"),
]

# --------------------------------------------------------------------------- #
#  Latin -> Cyrillic                                                           #
# --------------------------------------------------------------------------- #

_LAT_TO_CYR_PAIRS: list[tuple[str, str]] = [
    # digraphs first (before their component letters)
    ("Lj", "Љ"), ("LJ", "Љ"), ("lj", "љ"),
    ("Nj", "Њ"), ("NJ", "Њ"), ("nj", "њ"),
    ("Dž", "Џ"), ("DŽ", "Џ"), ("dž", "џ"),
    ("Đ", "Ђ"), ("đ", "ђ"),
    # uppercase
    ("A", "А"), ("B", "Б"), ("C", "Ц"), ("Č", "Ч"), ("Ć", "Ћ"),
    ("D", "Д"), ("E", "Е"), ("F", "Ф"), ("G", "Г"), ("H", "Х"),
    ("I", "И"), ("J", "Ј"), ("K", "К"), ("L", "Л"), ("M", "М"),
    ("N", "Н"), ("O", "О"), ("P", "П"), ("R", "Р"), ("S", "С"),
    ("Š", "Ш"), ("T", "Т"), ("U", "У"), ("V", "В"), ("Z", "З"),
    ("Ž", "Ж"),
    # lowercase
    ("a", "а"), ("b", "б"), ("c", "ц"), ("č", "ч"), ("ć", "ћ"),
    ("d", "д"), ("e", "е"), ("f", "ф"), ("g", "г"), ("h", "х"),
    ("i", "и"), ("j", "ј"), ("k", "к"), ("l", "л"), ("m", "м"),
    ("n", "н"), ("o", "о"), ("p", "п"), ("r", "р"), ("s", "с"),
    ("š", "ш"), ("t", "т"), ("u", "у"), ("v", "в"), ("z", "з"),
    ("ž", "ж"),
]

# --------------------------------------------------------------------------- #
#  ASCII folding (students type without diacritics)                            #
# --------------------------------------------------------------------------- #

_ASCII_FOLD_PAIRS: list[tuple[str, str]] = [
    ("Đ", "Dj"), ("đ", "dj"),
    ("Č", "C"), ("Ć", "C"), ("Š", "S"), ("Ž", "Z"),
    ("č", "c"), ("ć", "c"), ("š", "s"), ("ž", "z"),
]

# NB: dž -> dz is achieved by the ž -> z rule; Џ/џ -> Dž/dž first via cyr->lat.


_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
_DIACRITIC_LATIN_RE = re.compile(r"[čćšžđČĆŠŽĐ]")


def _replace_pairs(text: str, pairs: list[tuple[str, str]]) -> str:
    for src, dst in pairs:
        text = text.replace(src, dst)
    return text


def cyrillic_to_latin(text: str) -> str:
    return _replace_pairs(text, _CYR_TO_LAT_PAIRS)


def latin_to_cyrillic(text: str) -> str:
    return _replace_pairs(text, _LAT_TO_CYR_PAIRS)


def ascii_fold(text: str) -> str:
    """Fold Latin diacritics to ASCII (č->c, š->s, ž->z, ć->c, đ->dj)."""
    return _replace_pairs(text, _ASCII_FOLD_PAIRS)


def _detect_script(text: str) -> str:
    if _CYRILLIC_RE.search(text):
        return "cyrillic"
    if _DIACRITIC_LATIN_RE.search(text):
        return "latin"
    return "ascii"


def expand_variants(text: str) -> list[tuple[str, str, str]]:
    """
    Return up to three (text, variant_name, script) tuples for `text`:

        ("original", ...)       — exactly as extracted
        ("transliterated", ...) — the other Serbian script
        ("ascii", ...)          — diacritics folded

    Variants that would be identical to an earlier one are dropped.
    """
    variants: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    def add(candidate: str, name: str) -> None:
        if candidate and candidate not in seen:
            seen.add(candidate)
            variants.append((candidate, name, _detect_script(candidate)))

    add(text, "original")

    if _CYRILLIC_RE.search(text):
        add(cyrillic_to_latin(text), "transliterated")
    else:
        add(latin_to_cyrillic(text), "transliterated")

    # ASCII fold is always computed off the Latin surface form.
    latin_form = cyrillic_to_latin(text) if _CYRILLIC_RE.search(text) else text
    add(ascii_fold(latin_form), "ascii")

    return variants
