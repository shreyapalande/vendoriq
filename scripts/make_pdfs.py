"""Validate the synthetic vendor questionnaires in evals/vendors/*.yaml and render
each one to evals/pdfs/<vendor>.pdf.

Usage, from the repo root:
    .venv/bin/python scripts/make_pdfs.py        (Windows: .venv\\Scripts\\python scripts\\make_pdfs.py)

If any YAML file is invalid, nothing is rendered. Each YAML page becomes exactly
one PDF page, so the page numbers in each file's `expected` section match the
PDF; rendering fails if a page's content does not fit on one page.

YAML shape:
    vendor: "Name"
    pages:                       # page 1, page 2, ...
      - layout: single           # or two_column
        blocks:
          - {type: heading, text: "..."}
          - {type: paragraph, text: "..."}                 # wrapped by ReportLab
          - {type: lines, lines: ["...", "..."]}           # one PDF line per item, no re-wrapping
          - {type: table, rows: [["Header", ...], ["cell", ...]]}
          - {type: column_break}                           # two_column pages only
    expected:                    # one entry per control code
      MFA:
        label: SUPPORTED
        quotes:                  # exact supporting passages, each on its own page
          - {page: 2, text: "..."}
        why: "one line"
"""

import sys
from pathlib import Path
from xml.sax.saxutils import escape

import yaml
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    FrameBreak,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))  # so `app` imports work without packaging the backend

from app.ai.controls import CONTROL_CODES, Label  # noqa: E402
# The pipeline's normalizer, so the expected quotes are checked exactly the way the
# pipeline will compare text. Re-exported as make_pdfs.normalize for the tests.
from app.ai.text import normalize  # noqa: E402

VENDORS_DIR = REPO_ROOT / "evals" / "vendors"
PDFS_DIR = REPO_ROOT / "evals" / "pdfs"

LAYOUTS = {"single", "two_column"}
BLOCK_FIELDS = {  # block type -> the one field it needs besides `type`
    "heading": "text",
    "paragraph": "text",
    "lines": "lines",
    "table": "rows",
    "column_break": None,
}
EXPECTED_FIELDS = {"label", "quotes", "why"}
QUOTE_FIELDS = {"page", "text"}


# ---------------------------------------------------------------- text helpers


def page_text(page: dict) -> str:
    """All text on one YAML page in reading order, one line per block line or table cell."""
    parts: list[str] = []
    for block in page["blocks"]:
        kind = block["type"]
        if kind in ("heading", "paragraph"):
            parts.append(block["text"])
        elif kind == "lines":
            parts.extend(block["lines"])
        elif kind == "table":
            for row in block["rows"]:
                parts.extend(row)
    return "\n".join(parts)


class UniqueKeyLoader(yaml.SafeLoader):
    """yaml.SafeLoader that rejects duplicate keys in a mapping.

    Plain safe_load silently keeps the last of two equal keys, so a mis-indented or
    copy-pasted entry (two `MFA:` entries, or two `label:` keys) quietly overwrites
    ground truth instead of failing.
    """


def _construct_mapping_without_duplicates(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark, f"found duplicate key {key!r}", key_node.start_mark)
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_without_duplicates)


def load_vendor(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.load(f, Loader=UniqueKeyLoader)  # a SafeLoader subclass: no arbitrary objects


def load_all_vendors() -> dict[str, dict]:
    """File stem (used as the PDF name) -> parsed YAML."""
    return {path.stem: load_vendor(path) for path in sorted(VENDORS_DIR.glob("*.yaml"))}


# ------------------------------------------------------------------ validation


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


def _validate_page(number: int, page) -> list[str]:
    where = f"page {number}"
    if not isinstance(page, dict):
        return [f"{where}: must be a mapping"]
    errors = []
    if set(page) != {"layout", "blocks"}:
        errors.append(f"{where}: must have exactly the keys 'layout' and 'blocks'")
    layout = page.get("layout")
    if layout not in LAYOUTS:
        errors.append(f"{where}: layout must be one of {sorted(LAYOUTS)}")
    blocks = page.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return errors + [f"{where}: 'blocks' must be a non-empty list"]

    column_breaks = 0
    for i, block in enumerate(blocks, start=1):
        bwhere = f"{where}, block {i}"
        if not isinstance(block, dict) or block.get("type") not in BLOCK_FIELDS:
            errors.append(f"{bwhere}: 'type' must be one of {sorted(BLOCK_FIELDS)}")
            continue
        kind = block["type"]
        field = BLOCK_FIELDS[kind]
        allowed = {"type"} if field is None else {"type", field}
        if set(block) != allowed:
            errors.append(f"{bwhere}: a {kind} block must have exactly the keys {sorted(allowed)}")
            continue
        value = block.get(field)
        if kind in ("heading", "paragraph") and not (isinstance(value, str) and value.strip()):
            errors.append(f"{bwhere}: 'text' must be a non-empty string")
        elif kind == "lines" and not (_is_str_list(value) and value):
            errors.append(f"{bwhere}: 'lines' must be a non-empty list of strings")
        elif kind == "table":
            if not (isinstance(value, list) and value and all(_is_str_list(row) and row for row in value)):
                errors.append(f"{bwhere}: 'rows' must be a non-empty list of non-empty string lists")
            elif len({len(row) for row in value}) != 1:
                errors.append(f"{bwhere}: every table row must have the same number of cells")
        elif kind == "column_break":
            column_breaks += 1

    if column_breaks and layout != "two_column":
        errors.append(f"{where}: column_break is only allowed on two_column pages")
    if column_breaks > 1:
        errors.append(f"{where}: at most one column_break per page")
    return errors


def _validate_expected(code: str, entry, page_texts: dict[int, str]) -> list[str]:
    where = f"expected.{code}"
    if not isinstance(entry, dict):
        return [f"{where}: must be a mapping"]
    if set(entry) != EXPECTED_FIELDS:
        return [f"{where}: must have exactly the keys {sorted(EXPECTED_FIELDS)}, got unknown keys "
                f"{sorted(set(entry) - EXPECTED_FIELDS)} / missing keys {sorted(EXPECTED_FIELDS - set(entry))}"]

    errors = []
    try:
        label = Label(entry["label"])
    except ValueError:
        errors.append(f"{where}: label must be one of {[label.value for label in Label]}")
        label = None
    if not (isinstance(entry["why"], str) and entry["why"].strip()):
        errors.append(f"{where}: 'why' must be a non-empty string")
    quotes = entry["quotes"]
    if not isinstance(quotes, list):
        return errors + [f"{where}: 'quotes' must be a list"]

    for i, quote in enumerate(quotes, start=1):
        qwhere = f"{where}.quotes[{i}]"
        if not (isinstance(quote, dict) and set(quote) == QUOTE_FIELDS):
            errors.append(f"{qwhere}: must have exactly the keys {sorted(QUOTE_FIELDS)}")
            continue
        page, text = quote["page"], quote["text"]
        if type(page) is not int or page not in page_texts:
            errors.append(f"{qwhere}: page {page!r} does not exist (document has {len(page_texts)} pages)")
        elif not (isinstance(text, str) and text.strip()):
            errors.append(f"{qwhere}: 'text' must be a non-empty string")
        elif normalize(text) not in page_texts[page]:
            errors.append(f"{qwhere}: text not found on page {page}: {text!r}")

    if label == Label.MISSING_EVIDENCE and quotes:
        errors.append(f"{where}: MISSING_EVIDENCE must have no quotes")
    elif label in (Label.SUPPORTED, Label.INSUFFICIENT_EVIDENCE) and not quotes:
        errors.append(f"{where}: {label} needs at least one quote")
    elif label == Label.CONTRADICTORY_EVIDENCE and len(quotes) < 2:
        errors.append(f"{where}: CONTRADICTORY_EVIDENCE needs at least two quotes")
    return errors


def quote_pages(entry: dict) -> list[int]:
    """The sorted, distinct pages an expected entry's quotes are on (empty for MISSING_EVIDENCE)."""
    return sorted({quote["page"] for quote in entry["quotes"]})


def validate_vendor(vendor) -> list[str]:
    """Return a list of human-readable problems; an empty list means the file is valid."""
    if not isinstance(vendor, dict):
        return ["the file must be a mapping with 'vendor', 'pages' and 'expected'"]
    errors = []
    if set(vendor) != {"vendor", "pages", "expected"}:
        errors.append("top level must have exactly the keys 'vendor', 'pages' and 'expected'")
    if not (isinstance(vendor.get("vendor"), str) and vendor["vendor"].strip()):
        errors.append("'vendor' must be a non-empty string")

    pages = vendor.get("pages")
    if not isinstance(pages, list) or not pages:
        return errors + ["'pages' must be a non-empty list"]
    for number, page in enumerate(pages, start=1):
        errors.extend(_validate_page(number, page))
    if errors:
        return errors  # the quote checks below need well-formed pages

    expected = vendor.get("expected")
    if not isinstance(expected, dict):
        return ["'expected' must be a mapping of control code -> entry"]
    missing_codes = [code for code in CONTROL_CODES if code not in expected]
    unknown_codes = [code for code in expected if code not in CONTROL_CODES]
    if missing_codes:
        errors.append(f"expected: missing controls {missing_codes}")
    if unknown_codes:
        errors.append(f"expected: unknown controls {unknown_codes}")

    page_texts = {number: normalize(page_text(page)) for number, page in enumerate(pages, start=1)}
    for code, entry in expected.items():
        if code in CONTROL_CODES:
            errors.extend(_validate_expected(code, entry, page_texts))
    return errors


def validate_label_coverage(vendors: list[dict]) -> list[str]:
    """Across all (already valid) vendors combined, every label must be used at least once."""
    used = {entry["label"] for vendor in vendors for entry in vendor["expected"].values()}
    return [f"label {label.value} is not used by any vendor" for label in Label if label.value not in used]


# ------------------------------------------------------------------- rendering

PAGE_WIDTH, PAGE_HEIGHT = LETTER
MARGIN = 54  # 0.75 inch
COLUMN_GAP = 18
FRAME_PADDING = 6  # ReportLab's default padding inside a Frame, on each side
BODY_WIDTH = PAGE_WIDTH - 2 * MARGIN
BODY_HEIGHT = PAGE_HEIGHT - 2 * MARGIN
COLUMN_WIDTH = (BODY_WIDTH - COLUMN_GAP) / 2


def _page_templates(vendor_name: str, first_layout: str) -> list[PageTemplate]:
    def draw_header_and_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawString(MARGIN, PAGE_HEIGHT - MARGIN + 20, f"{vendor_name} - Security Questionnaire")
        canvas.drawRightString(PAGE_WIDTH - MARGIN, MARGIN - 24, f"Page {doc.page}")
        canvas.restoreState()

    single = PageTemplate(
        id="single",
        frames=[Frame(MARGIN, MARGIN, BODY_WIDTH, BODY_HEIGHT, id="body")],
        onPage=draw_header_and_footer,
    )
    two_column = PageTemplate(
        id="two_column",
        frames=[
            Frame(MARGIN, MARGIN, COLUMN_WIDTH, BODY_HEIGHT, id="left"),
            Frame(MARGIN + COLUMN_WIDTH + COLUMN_GAP, MARGIN, COLUMN_WIDTH, BODY_HEIGHT, id="right"),
        ],
        onPage=draw_header_and_footer,
    )
    # The document starts on the first template in the list, so put page 1's layout first.
    return [single, two_column] if first_layout == "single" else [two_column, single]


def _table(rows: list[list[str]], styles, width: float) -> Table:
    header_style = ParagraphStyle("TableHeader", parent=styles["BodyText"], fontName="Helvetica-Bold")
    cells = [
        [Paragraph(escape(cell), header_style if row_index == 0 else styles["BodyText"]) for cell in row]
        for row_index, row in enumerate(rows)
    ]
    column_count = len(rows[0])
    table = Table(cells, colWidths=[width / column_count] * column_count)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


def _flowables_for_page(page: dict, styles) -> list:
    frame_width = BODY_WIDTH if page["layout"] == "single" else COLUMN_WIDTH
    content_width = frame_width - 2 * FRAME_PADDING
    flowables = []
    for block in page["blocks"]:
        kind = block["type"]
        if kind == "heading":
            flowables.append(Paragraph(escape(block["text"]), styles["Heading2"]))
        elif kind == "paragraph":
            flowables.append(Paragraph(escape(block["text"]), styles["BodyText"]))
        elif kind == "lines":
            # An explicit <br/> after each line, so the line breaks (including a
            # hyphenated break inside a word) land exactly where the YAML puts them.
            joined = "<br/>".join(escape(line) for line in block["lines"])
            flowables.append(Paragraph(joined, styles["BodyText"]))
        elif kind == "table":
            flowables.append(_table(block["rows"], styles, content_width))
            flowables.append(Spacer(1, 8))
        elif kind == "column_break":
            flowables.append(FrameBreak())
    return flowables


def render_pdf(vendor: dict, out_path: Path) -> None:
    """Render one validated vendor to a PDF with exactly one PDF page per YAML page."""
    pages = vendor["pages"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(
        str(out_path),
        pagesize=LETTER,
        title=f"{vendor['vendor']} Security Questionnaire",
        author=vendor["vendor"],
    )
    doc.addPageTemplates(_page_templates(vendor["vendor"], pages[0]["layout"]))
    styles = getSampleStyleSheet()

    story = []
    for index, page in enumerate(pages):
        if index > 0:
            story.append(NextPageTemplate(page["layout"]))
            story.append(PageBreak())
        story.extend(_flowables_for_page(page, styles))
    doc.build(story)

    if doc.page != len(pages):
        out_path.unlink()
        raise ValueError(
            f"{out_path.name}: rendered {doc.page} PDF pages but the YAML has {len(pages)}; "
            "some page's content is too long to fit on one page"
        )


def main() -> int:
    vendors = load_all_vendors()
    if not vendors:
        print(f"No vendor files found in {VENDORS_DIR}", file=sys.stderr)
        return 1

    errors = []
    for name, vendor in vendors.items():
        errors.extend(f"{name}.yaml: {error}" for error in validate_vendor(vendor))
    if not errors:
        errors.extend(validate_label_coverage(list(vendors.values())))
    if errors:
        print("Invalid vendor files, nothing rendered:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    for name, vendor in vendors.items():
        out_path = PDFS_DIR / f"{name}.pdf"
        render_pdf(vendor, out_path)
        print(f"wrote {out_path.relative_to(REPO_ROOT)} ({len(vendor['pages'])} pages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
