"""Find a model's quote in the document, so only quotes that are really there are
shown as evidence (D-02). The model returns {page, quote}; this code computes where
the quote is.

Offsets (start, end) are character positions in normalize(page_text), not in the
raw PDF text. They are only valid with the same NORMALIZER_VERSION.

Plain Python only (no FastAPI or database imports).
"""

from dataclasses import dataclass
from enum import StrEnum

from rapidfuzz import fuzz

from app.ai.text import normalize

FUZZY_THRESHOLD = 90

# Quotes shorter than this (after normalizing) must match exactly. partial_ratio
# compares the quote with its best-matching window of the page, so a short quote
# like "Yes, we do." scores >= 90 against almost any page.
MIN_FUZZY_LENGTH = 20


class Match(StrEnum):
    EXACT = "exact"
    FUZZY = "fuzzy"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class VerifiedCitation:
    page: int           # 1-based; where the quote was found (the cited page if unverified)
    start: int | None   # offsets into normalize(pages[page - 1]); None if unverified
    end: int | None
    match: Match


def verify_citation(quote: str, cited_page: int, pages: list[str]) -> VerifiedCitation:
    """Locate `quote` in the document. `pages` is raw page text, index 0 = page 1.

    1. Exact match on the cited page.
    2. Exact match on any other page (the model sometimes gets the page wrong).
    3. Fuzzy match (rapidfuzz partial_ratio >= FUZZY_THRESHOLD) on every page; the
       best score wins, and the cited page wins a tie.
    Otherwise the citation is unverified and must not be shown as evidence.
    """
    wanted = normalize(quote)
    normalized_pages = [normalize(page) for page in pages]
    unverified = VerifiedCitation(cited_page, None, None, Match.UNVERIFIED)
    if not wanted:
        return unverified

    # Cited page first, then the others in page order. An out-of-range cited page
    # (the model invented a page number) just means: search every page.
    page_numbers = list(range(1, len(pages) + 1))
    if cited_page in page_numbers:
        page_numbers.remove(cited_page)
        page_numbers.insert(0, cited_page)

    for number in page_numbers:
        start = normalized_pages[number - 1].find(wanted)
        if start != -1:
            return VerifiedCitation(number, start, start + len(wanted), Match.EXACT)

    if len(wanted) < MIN_FUZZY_LENGTH:
        return unverified

    best = None  # (score, page number, alignment)
    for number in page_numbers:  # cited page first, so it keeps a tie (strict > below)
        alignment = fuzz.partial_ratio_alignment(wanted, normalized_pages[number - 1])
        if alignment.score >= FUZZY_THRESHOLD and (best is None or alignment.score > best[0]):
            best = (alignment.score, number, alignment)
    if best is None:
        return unverified
    _, number, alignment = best
    # partial_ratio_alignment(quote, page): src_* is the quote, dest_* is the page span.
    return VerifiedCitation(number, alignment.dest_start, alignment.dest_end, Match.FUZZY)
