"""Extraction tests built on synthetic DoclingDocuments.

These construct a DoclingDocument directly instead of converting a real PDF,
so they need only docling-core (no model weights, no OCR) and still exercise
the exact traversal the job runs. `_FakeConversion` stands in for docling's
ConversionResult, which is just a container for the document and status.
"""
import asyncio
import pytest
from docling_core.types.doc import BoundingBox, CoordOrigin, DocItemLabel, Size
from docling_core.types.doc.document import (
    DoclingDocument,
    ProvenanceItem,
    TableCell,
    TableData,
)

from docling_service import (  # noqa: E402
    ExtractionResult,
    _conversion_warning,
    _segments_from_result,
    _text_layer_looks_empty,
    extract_segments_with_docling,
)


class _FakeConversion:
    def __init__(self, document, status="success", errors=None):
        self.document = document
        self.status = status
        self.errors = errors or []


class _FakeError:
    def __init__(self, message):
        self.error_message = message


class _FakeConverter:
    """Stands in for a DocumentConverter; returns a canned conversion."""

    def __init__(self, conversion):
        self.conversion = conversion
        self.calls = 0

    def convert(self, _path):
        self.calls += 1
        return self.conversion


def _prov(page_no):
    return ProvenanceItem(
        page_no=page_no,
        bbox=BoundingBox(l=0, t=0, r=10, b=10, coord_origin=CoordOrigin.BOTTOMLEFT),
        charspan=(0, 0),
    )


def _table_data(rows):
    cells = [
        TableCell(
            text=value,
            row_span=1,
            col_span=1,
            start_row_offset_idx=r,
            end_row_offset_idx=r + 1,
            start_col_offset_idx=c,
            end_col_offset_idx=c + 1,
            column_header=(r == 0),
        )
        for r, row in enumerate(rows)
        for c, value in enumerate(row)
    ]
    return TableData(num_rows=len(rows), num_cols=len(rows[0]), table_cells=cells)


def _doc_with_pages(page_count=1):
    doc = DoclingDocument(name="test")
    for page_no in range(1, page_count + 1):
        doc.add_page(page_no=page_no, size=Size(width=612, height=792))
    return doc


PRICING_ROWS = [["Plan", "Price"], ["Annual", "USD 4200"]]


def _extracted_text(document):
    return "\n".join(s.text for s in _segments_from_result(_FakeConversion(document)))


# --- tables -----------------------------------------------------------------


def test_table_content_survives_alongside_prose():
    """The regression this suite exists for: a TableItem has no `.text`, so a
    traversal that reads only `.text` drops every table in the document."""
    doc = _doc_with_pages()
    doc.add_heading(text="Pricing", level=1, prov=_prov(1))
    doc.add_text(label=DocItemLabel.TEXT, text="Rates effective 2026.", prov=_prov(1))
    doc.add_table(data=_table_data(PRICING_ROWS), prov=_prov(1))
    doc.add_text(label=DocItemLabel.TEXT, text="Contact sales.", prov=_prov(1))

    text = _extracted_text(doc)
    assert "USD 4200" in text
    assert "Annual" in text
    # Surrounding prose and heading are still there.
    assert "Rates effective 2026." in text
    assert "Pricing" in text


def test_table_only_document_still_extracts():
    """A document that is nothing but a table has no text items at all and
    falls through to the flat markdown export."""
    doc = _doc_with_pages()
    doc.add_table(data=_table_data(PRICING_ROWS), prov=_prov(1))

    assert "USD 4200" in _extracted_text(doc)


def test_table_keeps_page_and_heading_provenance():
    doc = _doc_with_pages(page_count=2)
    doc.add_heading(text="Fees", level=1, prov=_prov(2))
    doc.add_table(data=_table_data(PRICING_ROWS), prov=_prov(2))

    segments = _segments_from_result(_FakeConversion(doc))
    table_segments = [s for s in segments if "USD 4200" in s.text]
    assert table_segments, "table content missing from segments"
    assert table_segments[0].page_number == 2
    assert table_segments[0].heading == "Fees"


def test_picture_placeholder_is_not_emitted():
    """PictureItem.export_to_markdown returns an image placeholder comment,
    which must not end up in the embedded text."""
    doc = _doc_with_pages()
    doc.add_text(label=DocItemLabel.TEXT, text="Figure follows.", prov=_prov(1))
    doc.add_picture(prov=_prov(1))

    text = _extracted_text(doc)
    assert "Image not available" not in text
    assert "<!--" not in text
    assert "Figure follows." in text


def test_table_caption_is_captured_exactly_once():
    doc = _doc_with_pages()
    caption = doc.add_text(label=DocItemLabel.CAPTION, text="Table 1: rates", prov=_prov(1))
    doc.add_table(data=_table_data(PRICING_ROWS), caption=caption, prov=_prov(1))

    assert _extracted_text(doc).count("Table 1: rates") == 1


# --- incomplete conversions -------------------------------------------------


def test_partial_success_produces_a_warning():
    """Docling returns PARTIAL_SUCCESS without raising, so an unchecked
    conversion records a truncated document as a clean one."""
    conversion = _FakeConversion(
        _doc_with_pages(), status="partial_success", errors=[_FakeError("Page 12 failed to parse.")]
    )
    warning = _conversion_warning(conversion, "report.pdf")

    assert warning is not None
    assert "report.pdf" in warning
    assert "Page 12 failed to parse." in warning


def test_successful_conversion_produces_no_warning():
    assert _conversion_warning(_FakeConversion(_doc_with_pages()), "report.pdf") is None


def test_warning_truncates_long_error_lists():
    conversion = _FakeConversion(
        _doc_with_pages(),
        status="partial_success",
        errors=[_FakeError(f"Page {n} failed to parse.") for n in range(1, 26)],
    )
    warning = _conversion_warning(conversion, "big.pdf")
    assert "+15 more" in warning


def test_warning_reaches_the_caller():
    doc = _doc_with_pages()
    doc.add_text(label=DocItemLabel.TEXT, text="x" * 500, prov=_prov(1))
    converter = _FakeConverter(
        _FakeConversion(doc, status="partial_success", errors=[_FakeError("Page 3 failed to parse.")])
    )

    result = asyncio.run(
        extract_segments_with_docling(file_name="a.pdf", file_bytes=b"%PDF-", converter=converter)
    )
    assert isinstance(result, ExtractionResult)
    assert result.warning is not None
    assert "Page 3 failed to parse." in result.warning
    assert result.segments


def test_empty_extraction_still_raises():
    converter = _FakeConverter(_FakeConversion(_doc_with_pages()))
    with pytest.raises(RuntimeError, match="did not produce parseable text"):
        asyncio.run(
            extract_segments_with_docling(file_name="a.pdf", file_bytes=b"%PDF-", converter=converter)
        )


# --- empty text layer detection --------------------------------------------


def test_scanned_pdf_is_flagged_for_full_page_ocr():
    """A PDF whose text layer is broken or absent yields almost nothing, and
    docling will not OCR it because it sees no bitmap region to work on."""
    doc = _doc_with_pages(page_count=10)
    doc.add_text(label=DocItemLabel.TEXT, text="Page 1", prov=_prov(1))

    segments = _segments_from_result(_FakeConversion(doc))
    assert _text_layer_looks_empty(_FakeConversion(doc), segments, "scan.pdf")


def test_normal_pdf_is_not_flagged():
    doc = _doc_with_pages(page_count=2)
    for page in (1, 2):
        doc.add_text(label=DocItemLabel.TEXT, text="word " * 400, prov=_prov(page))

    segments = _segments_from_result(_FakeConversion(doc))
    assert not _text_layer_looks_empty(_FakeConversion(doc), segments, "report.pdf")


def test_non_paginated_format_is_never_flagged():
    """docx/html/md report zero pages and have no OCR pipeline to retry with."""
    doc = DoclingDocument(name="memo")
    doc.add_text(label=DocItemLabel.TEXT, text="hi")

    segments = _segments_from_result(_FakeConversion(doc))
    assert not _text_layer_looks_empty(_FakeConversion(doc), segments, "memo.docx")


def test_threshold_is_configurable(monkeypatch):
    doc = _doc_with_pages(page_count=2)
    for page in (1, 2):
        doc.add_text(label=DocItemLabel.TEXT, text="word " * 400, prov=_prov(page))
    segments = _segments_from_result(_FakeConversion(doc))

    monkeypatch.setenv("DOCLING_MIN_CHARS_PER_PAGE", "100000")
    assert _text_layer_looks_empty(_FakeConversion(doc), segments, "report.pdf")


# --- existing behaviour that must not regress -------------------------------


def test_segments_split_on_page_boundaries():
    doc = _doc_with_pages(page_count=2)
    doc.add_text(label=DocItemLabel.TEXT, text="First page body.", prov=_prov(1))
    doc.add_text(label=DocItemLabel.TEXT, text="Second page body.", prov=_prov(2))

    segments = _segments_from_result(_FakeConversion(doc))
    assert [s.page_number for s in segments] == [1, 2]


def test_heading_breadcrumb_is_nested():
    doc = _doc_with_pages()
    doc.add_heading(text="Refund Policy", level=1, prov=_prov(1))
    doc.add_heading(text="Annual Plans", level=2, prov=_prov(1))
    doc.add_text(label=DocItemLabel.TEXT, text="The limit is 30 days.", prov=_prov(1))

    segments = _segments_from_result(_FakeConversion(doc))
    body = [s for s in segments if "30 days" in s.text]
    assert body[0].heading == "Refund Policy > Annual Plans"


# --- full-page OCR retry ----------------------------------------------------


def _sparse_pdf_doc():
    """10 pages with almost no extractable text — a scanned or broken-text-layer PDF."""
    doc = _doc_with_pages(page_count=10)
    doc.add_text(label=DocItemLabel.TEXT, text="1", prov=_prov(1))
    return doc


def _rich_pdf_doc():
    doc = _doc_with_pages(page_count=10)
    for page in range(1, 11):
        doc.add_text(label=DocItemLabel.TEXT, text="recovered " * 100, prov=_prov(page))
    return doc


def _patch_ocr_converter(monkeypatch, first, retry):
    """Route the two shared-converter variants to canned conversions."""
    converters = {False: _FakeConverter(first), True: _FakeConverter(retry)}
    monkeypatch.setattr("docling_service._shared_converter", lambda force: converters[force])
    return converters


def test_empty_text_layer_triggers_full_page_ocr_retry(monkeypatch):
    converters = _patch_ocr_converter(
        monkeypatch, _FakeConversion(_sparse_pdf_doc()), _FakeConversion(_rich_pdf_doc())
    )

    result = asyncio.run(extract_segments_with_docling(file_name="scan.pdf", file_bytes=b"%PDF-"))

    assert result.used_full_page_ocr
    assert converters[True].calls == 1
    assert "recovered" in "\n".join(s.text for s in result.segments)


def test_retry_is_discarded_when_it_finds_no_extra_text(monkeypatch):
    first = _FakeConversion(_sparse_pdf_doc())
    _patch_ocr_converter(monkeypatch, first, _FakeConversion(_doc_with_pages(page_count=10)))

    result = asyncio.run(extract_segments_with_docling(file_name="scan.pdf", file_bytes=b"%PDF-"))

    assert not result.used_full_page_ocr
    assert result.segments


def test_retry_failure_keeps_the_first_pass(monkeypatch):
    class _Exploding:
        def convert(self, _path):
            raise RuntimeError("OCR model unavailable")

    converters = {False: _FakeConverter(_FakeConversion(_sparse_pdf_doc())), True: _Exploding()}
    monkeypatch.setattr("docling_service._shared_converter", lambda force: converters[force])

    result = asyncio.run(extract_segments_with_docling(file_name="scan.pdf", file_bytes=b"%PDF-"))

    assert not result.used_full_page_ocr
    assert result.segments


def test_healthy_pdf_does_not_trigger_a_retry(monkeypatch):
    converters = _patch_ocr_converter(
        monkeypatch, _FakeConversion(_rich_pdf_doc()), _FakeConversion(_rich_pdf_doc())
    )

    result = asyncio.run(extract_segments_with_docling(file_name="report.pdf", file_bytes=b"%PDF-"))

    assert converters[True].calls == 0
    assert not result.used_full_page_ocr


def test_explicit_converter_disables_the_retry(monkeypatch):
    converters = _patch_ocr_converter(
        monkeypatch, _FakeConversion(_rich_pdf_doc()), _FakeConversion(_rich_pdf_doc())
    )
    explicit = _FakeConverter(_FakeConversion(_sparse_pdf_doc()))

    result = asyncio.run(
        extract_segments_with_docling(file_name="scan.pdf", file_bytes=b"%PDF-", converter=explicit)
    )

    assert explicit.calls == 1
    assert converters[True].calls == 0
    assert not result.used_full_page_ocr
