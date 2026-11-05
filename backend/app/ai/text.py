"""Extract page text from a PDF, and normalize text so quotes can be compared with it.

Plain Python only (no FastAPI or database imports).
"""

import re
import unicodedata
from pathlib import Path

import pymupdf

# Bump when normalize() changes: citation offsets are positions in normalized text,
# so offsets stored under one version are not valid under another.
NORMALIZER_VERSION = "1"

MAX_PAGES = 30

# Curly quotes -> straight quotes. NFKC leaves these alone.
_QUOTE_TABLE = str.maketrans({
    "‘": "'", "’": "'",  # ‘ ’
    "“": '"', "”": '"',  # “ ”
})

# A word split by a hyphen at a line break: "encryp-\ntion" -> "encryption".
# Only between two lowercase letters, so "AES-256" and "TLS-\n1.2" are left alone.
_HYPHEN_BREAK = re.compile(r"([a-z])-[ \t]*\n\s*([a-z])")


class PdfTextError(ValueError):
    """The PDF cannot be used: not a PDF, too many pages, or no extractable text."""


def extract_pages(pdf_path: str | Path) -> list[str]:
    """Return the raw text of each page (index 0 = page 1). Not normalized."""
    try:
        doc = pymupdf.open(pdf_path, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several types for corrupt or non-PDF files
        raise PdfTextError(f"not a readable PDF: {exc}") from exc

    with doc:
        if doc.page_count > MAX_PAGES:  # check before extracting anything
            raise PdfTextError(f"PDF has {doc.page_count} pages; the limit is {MAX_PAGES}")
        pages = [page.get_text() for page in doc]

    if not any(page.strip() for page in pages):
        raise PdfTextError("PDF has no extractable text (it may be a scanned image)")
    return pages


def normalize(text: str) -> str:
    """Make text comparable across PDF extraction and model quotes.

    1. Unicode NFKC (ligatures like "ﬁ" -> "fi", non-breaking spaces -> spaces).
    2. Curly quotes -> straight quotes.
    3. Rejoin words split by a hyphen at a line break.
    4. Collapse all runs of whitespace to a single space and strip the ends.
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_QUOTE_TABLE)
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    return " ".join(text.split())
