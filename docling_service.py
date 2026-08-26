import logging
import tempfile
from pathlib import Path

from chunking import DocumentSegment

logger = logging.getLogger(__name__)

# Docling item labels that start a new section rather than contribute body text.
_HEADING_LABELS = {"title", "section_header"}


def create_docling_converter():
    """Create a DocumentConverter once to be reused across multiple files.

    Model loading (layout, table structure, OCR) happens here, so calling
    this once and passing the converter to extract_segments_with_docling
    saves significant time when processing a batch of files.
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
    return "\n\n".join(segment.text for segment in segments if segment.text.strip())


async def extract_segments_with_docling(
    file_name: str,
    file_bytes: bytes,
    converter=None,
) -> list[DocumentSegment]:
    """Return ordered segments for the document, split by page and section.

    Falls back to a single unstructured segment if neither page provenance
    nor headings are available for the source format.
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


def _segments_from_result(result: object) -> list[DocumentSegment]:
    document = getattr(result, "document", None)
    if document is None:
        return []

    structured_segments = _structured_segments(document)
    if structured_segments:
        return structured_segments

    fallback_text = _fallback_text(document)
    if fallback_text:
        return [DocumentSegment(page_number=None, text=fallback_text)]
    return []


def _structured_segments(document: object) -> list[DocumentSegment]:
    iterate_items = getattr(document, "iterate_items", None)
    if not callable(iterate_items):
        return []

    segments: list[DocumentSegment] = []
    heading_stack: list[tuple[int, str]] = []
    current_page: int | None = None
    current_heading: str | None = None
    current_lines: list[str] = []

    try:
        for entry in iterate_items():
            item = entry[0] if isinstance(entry, tuple) else entry
            text = getattr(item, "text", None)
            if not isinstance(text, str) or not text.strip():
                continue

            label = _item_label(item)
            page_no = _item_page_number(item)

            if label in _HEADING_LABELS:
                if current_lines:
                    segments.append(
                        DocumentSegment(
                            page_number=current_page,
                            text="\n".join(current_lines),
                            heading=current_heading,
                        )
                    )
                _push_heading(heading_stack, _heading_level(item, label), text.strip())
                current_heading = _heading_path(heading_stack)
                current_page = page_no
                # Keep the heading itself in the body so it is embedded with
                # the section it introduces.
                current_lines = [text]
                continue

            if page_no != current_page and current_lines:
                segments.append(
                    DocumentSegment(
                        page_number=current_page,
                        text="\n".join(current_lines),
                        heading=current_heading,
                    )
                )
                current_lines = []
            current_page = page_no
            current_lines.append(text)
    except Exception as exc:
        logger.warning("Structured extraction failed; falling back to flat text: %s", exc)
        return []

    if current_lines:
        segments.append(
            DocumentSegment(
                page_number=current_page,
                text="\n".join(current_lines),
                heading=current_heading,
            )
        )

    # If we found neither pages nor headings there is nothing structured to
    # keep — the caller's flat export is richer, so let it take over.
    if len(segments) == 1 and segments[0].page_number is None and segments[0].heading is None:
        return []
    return segments


def _item_label(item: object) -> str:
    label = getattr(item, "label", None)
    if label is None:
        return ""
    value = getattr(label, "value", label)
    return str(value).strip().lower()


def _item_page_number(item: object) -> int | None:
    prov = getattr(item, "prov", None)
    if not prov:
        return None
    return getattr(prov[0], "page_no", None)


def _heading_level(item: object, label: str) -> int:
    if label == "title":
        return 0
    level = getattr(item, "level", None)
    if isinstance(level, int) and level > 0:
        return level
    return 1


def _push_heading(stack: list[tuple[int, str]], level: int, text: str) -> None:
    while stack and stack[-1][0] >= level:
        stack.pop()
    stack.append((level, text))


def _heading_path(stack: list[tuple[int, str]]) -> str | None:
    if not stack:
        return None
    return " > ".join(text for _, text in stack)


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
