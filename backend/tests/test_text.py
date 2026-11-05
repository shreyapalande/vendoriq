"""M1b: app/ai/text.py. No API calls."""

from pathlib import Path

import pymupdf
import pytest

from app.ai.text import MAX_PAGES, PdfTextError, extract_pages, normalize

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- normalize


def test_rejoins_a_hyphenated_line_break():
    raw = "protected with AES-256 encryp-\ntion at rest, using keys"
    assert normalize(raw) == "protected with AES-256 encryption at rest, using keys"


def test_keeps_real_hyphens():
    assert normalize("AES-256 and TLS-\n1.2 and SOC-2") == "AES-256 and TLS- 1.2 and SOC-2"


def test_straightens_curly_quotes():
    assert normalize("“We’re SOC 2” and ‘audited’") == "\"We're SOC 2\" and 'audited'"


def test_applies_nfkc():
    # "ﬁ" ligature -> "fi"; non-breaking space -> space; full-width digits -> ASCII
    assert normalize("conﬁguration of TLS １.２") == "configuration of TLS 1.2"


def test_collapses_whitespace_and_strips():
    assert normalize("  All   data\n\n is\tencrypted \n") == "All data is encrypted"


def test_is_idempotent():
    once = normalize("encryp-\ntion “at”  rest")
    assert normalize(once) == once


# ------------------------------------------------------------ extract_pages


def _make_pdf(path: Path, page_texts: list[str]) -> Path:
    doc = pymupdf.open()
    for text in page_texts:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()
    return path


def test_extracts_one_string_per_page(tmp_path):
    pdf = _make_pdf(tmp_path / "two.pdf", ["First page text", "Second page text"])
    pages = extract_pages(pdf)
    assert len(pages) == 2
    assert normalize(pages[0]) == "First page text"
    assert normalize(pages[1]) == "Second page text"


def test_extracts_a_generated_vendor_pdf_with_its_hyphenated_break():
    pages = extract_pages(REPO_ROOT / "evals" / "pdfs" / "bluefin_payroll.pdf")
    assert len(pages) == 5
    assert "encryp-\ntion at rest" in pages[1]  # raw text keeps the break...
    assert "AES-256 encryption at rest" in normalize(pages[1])  # ...normalize rejoins it


def test_accepts_exactly_the_page_limit(tmp_path):
    pdf = _make_pdf(tmp_path / "max.pdf", ["text"] * MAX_PAGES)
    assert len(extract_pages(pdf)) == MAX_PAGES


def test_rejects_too_many_pages(tmp_path):
    pdf = _make_pdf(tmp_path / "long.pdf", ["text"] * (MAX_PAGES + 1))
    with pytest.raises(PdfTextError, match="31 pages"):
        extract_pages(pdf)


def test_rejects_a_pdf_with_no_text(tmp_path):
    pdf = _make_pdf(tmp_path / "blank.pdf", ["", ""])
    with pytest.raises(PdfTextError, match="no extractable text"):
        extract_pages(pdf)


def test_rejects_a_file_that_is_not_a_pdf(tmp_path):
    fake = tmp_path / "fake.pdf"
    fake.write_text("this is not a PDF")
    with pytest.raises(PdfTextError, match="not a readable PDF"):
        extract_pages(fake)
