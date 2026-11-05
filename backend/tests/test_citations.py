"""M1b: app/ai/citations.py. No API calls."""

from app.ai.citations import Match, verify_citation
from app.ai.text import normalize

PAGES = [
    # page 1
    "1. About Acme\nAcme runs payroll for small businesses.\n"
    "Acme completed a SOC 2 Type I assessment in 2022.",
    # page 2: hyphenated break inside the key phrase, as in bluefin_payroll.pdf
    "3. Data Protection\nAll customer payroll data is protected with AES-256 encryp-\n"
    "tion at rest, using keys held in a dedicated key management\nservice and rotated every year.\n"
    "Multi-factor authentication is required for all staff.",
    # page 3: curly quotes in the PDF text
    "We call this our “zero trust” model, and it’s reviewed every quarter.",
]


def span(result) -> str:
    """The normalized page text the citation points at."""
    return normalize(PAGES[result.page - 1])[result.start:result.end]


def test_exact_match_on_the_cited_page():
    result = verify_citation("Multi-factor authentication is required for all staff.", 2, PAGES)
    assert (result.page, result.match) == (2, Match.EXACT)
    assert span(result) == "Multi-factor authentication is required for all staff."


def test_quote_on_the_wrong_page_is_found_on_the_right_page():
    result = verify_citation("Acme completed a SOC 2 Type I assessment in 2022.", 3, PAGES)
    assert (result.page, result.match) == (1, Match.EXACT)
    assert span(result) == "Acme completed a SOC 2 Type I assessment in 2022."


def test_hyphenated_phrase_matches_across_the_line_break():
    quote = "protected with AES-256 encryption at rest, using keys held in a dedicated key management service"
    result = verify_citation(quote, 2, PAGES)
    assert (result.page, result.match) == (2, Match.EXACT)
    assert span(result) == quote


def test_curly_quotes_on_either_side_still_match_exactly():
    straight_quote = "We call this our \"zero trust\" model, and it's reviewed every quarter."
    assert verify_citation(straight_quote, 3, PAGES).match == Match.EXACT
    curly_quote = "our “zero trust” model, and it’s reviewed"
    assert verify_citation(curly_quote, 3, PAGES).match == Match.EXACT


def test_a_small_slip_is_a_fuzzy_match_with_offsets_on_the_right_text():
    # model wrote "keys kept in" instead of "keys held in"
    quote = "AES-256 encryption at rest, using keys kept in a dedicated key management service"
    result = verify_citation(quote, 2, PAGES)
    assert (result.page, result.match) == (2, Match.FUZZY)
    assert "AES-256 encryption at rest" in span(result)
    assert "key management service" in span(result)


def test_an_invented_quote_is_unverified():
    result = verify_citation("All backups are encrypted and tested every month in a second region.", 2, PAGES)
    assert result.match == Match.UNVERIFIED
    assert (result.page, result.start, result.end) == (2, None, None)


def test_a_short_quote_is_not_fuzzy_matched():
    # "MFA is required." is not on any page; partial_ratio alone would score it high.
    result = verify_citation("MFA is required.", 2, PAGES)
    assert result.match == Match.UNVERIFIED


def test_a_short_quote_can_still_match_exactly():
    result = verify_citation("SOC 2 Type I", 1, PAGES)
    assert (result.page, result.match) == (1, Match.EXACT)


def test_an_out_of_range_page_searches_every_page():
    result = verify_citation("Acme runs payroll for small businesses.", 99, PAGES)
    assert (result.page, result.match) == (1, Match.EXACT)


def test_an_empty_quote_is_unverified():
    assert verify_citation("   ", 1, PAGES).match == Match.UNVERIFIED
