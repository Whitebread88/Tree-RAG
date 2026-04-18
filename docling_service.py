import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def create_docling_converter():
    """Create a DocumentConverter once to be reused across multiple files.

    Model loading (layout, table structure, OCR) happens here, so calling
    this once and passing the converter to extract_text_with_docling saves
    significant time when processing a batch of files.
    """
    try:
        from docling.document_converter import DocumentConverter
    except Exception as exc:
        raise RuntimeError("docling library is not available") from exc

    return DocumentConverter()


async def extract_text_with_docling(file_name: str, file_bytes: bytes, converter=None) -> str:
    segments = await extract_segments_with_docling(
        file_name=file_name,
        file_bytes=file_bytes,
        converter=converter,
    )
    return "\n\n".join(text for _, text in segments if text.strip())


async def extract_segments_with_docling(
    file_name: str,
    file_bytes: bytes,
    converter=None,
) -> list[tuple[int | None, str]]:
    """Return ordered (page_number, text) segments for the document.

    Falls back to a single (None, text) segment if per-page extraction
    isn't possible for the source format.
    """
    if converter is None:
        converter = create_docling_converter()

    suffix = Path(file_name).suffix or ".bin"

    with tempfile.TemporaryDirectory(prefix="docling-") as tmp_dir:
        input_path = Path(tmp_dir) / f"input{suffix}"
        input_path.write_bytes(file_bytes)

        result = converter.convert(str(input_path))
        segments = _segments_from_result(result)
        if not segments:
            raise RuntimeError("docling did not produce parseable text output")
        return segments


def _segments_from_result(result: object) -> list[tuple[int | None, str]]:
    document = getattr(result, "document", None)
    if document is None:
        return []

    paged_segments = _segments_by_page(document)
    if paged_segments:
        return paged_segments

    fallback_text = _fallback_text(document)
    if fallback_text:
        return [(None, fallback_text)]
    return []


def _segments_by_page(document: object) -> list[tuple[int | None, str]]:
    iterate_items = getattr(document, "iterate_items", None)
    if not callable(iterate_items):
        return []

    segments: list[tuple[int | None, str]] = []
    current_page: int | None = None
    current_lines: list[str] = []

    try:
        for entry in iterate_items():
            item = entry[0] if isinstance(entry, tuple) else entry
            text = getattr(item, "text", None)
            if not isinstance(text, str) or not text.strip():
                continue

            page_no: int | None = None
            prov = getattr(item, "prov", None)
            if prov:
                page_no = getattr(prov[0], "page_no", None)

            if page_no != current_page and current_lines:
                segments.append((current_page, "\n".join(current_lines)))
                current_lines = []
            current_page = page_no
            current_lines.append(text)
    except Exception as exc:
        logger.warning("Per-page extraction failed; falling back to flat text: %s", exc)
        return []

    if current_lines:
        segments.append((current_page, "\n".join(current_lines)))

    # Drop the page-less fallback if every item lacked provenance — caller will
    # fall back to a flat extraction and surface page_number=None per chunk.
    if len(segments) == 1 and segments[0][0] is None:
        return []
    return segments


def _fallback_text(document: object) -> str:
    for exporter_name in ("export_to_markdown", "export_to_text"):
        exporter = getattr(document, exporter_name, None)
        if callable(exporter):
            value = exporter()
            if isinstance(value, str) and value.strip():
                return value

    text_attr = getattr(document, "text", None)
    if isinstance(text_attr, str) and text_attr.strip():
        return text_attr

    return ""
