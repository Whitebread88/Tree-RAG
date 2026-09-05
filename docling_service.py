import logging
import os
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from chunking import DocumentSegment

logger = logging.getLogger(__name__)

# Docling item labels that start a new section rather than contribute body text.
_HEADING_LABELS = {"title", "section_header"}

# Tables carry their content in a cell grid, not in a `text` attribute, so they
# need to be rendered separately or their contents are lost entirely.
_TABLE_LABEL = "table"

# Below this many characters per page we assume the PDF's text layer is broken
# or absent (scanned pages, fonts without a usable ToUnicode CMap, text drawn
# as vector paths) and retry the whole document with full-page OCR. Docling
# only OCRs detected bitmap regions by default, so those pages otherwise come
# back empty without any error.
_DEFAULT_MIN_CHARS_PER_PAGE = 64


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Ignoring non-integer %s=%r; using %s", name, raw, default)
        return default


def _env_float_or_none(name: str) -> float | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("Ignoring non-numeric %s=%r; no timeout applied", name, raw)
        return None


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class ExtractionResult:
    """Segments plus anything that went wrong while producing them.

    `warning` is set when docling returned a document it could not fully
    parse. The segments are still usable, but they do not represent the whole
    file, so the caller records the warning rather than treating the result as
    a clean extraction.
    """

    segments: list[DocumentSegment] = field(default_factory=list)
    warning: str | None = None
    used_full_page_ocr: bool = False


def create_docling_converter(force_full_page_ocr: bool = False):
    """Build a DocumentConverter with an explicitly pinned PDF pipeline.

    Every option that affects how much text comes out is set here rather than
    inherited. Docling's default `OcrAutoOptions` picks an engine by probing
    which optional dependencies happen to be installed, and silently does no
    OCR at all when it finds none, so the engine is named explicitly and
    `requirements-job.txt` installs the matching extra.
    """
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except Exception as exc:
        raise RuntimeError("docling library is not available") from exc

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = True
    pipeline_options.do_table_structure = True
    pipeline_options.table_structure_options.do_cell_matching = True
    # onnxruntime backend, installed via the `rapidocr` extra.
    pipeline_options.ocr_options = RapidOcrOptions(force_full_page_ocr=force_full_page_ocr)
    # Off by default. When set, docling stops early and reports PARTIAL_SUCCESS,
    # which _conversion_warning below turns into a recorded warning.
    pipeline_options.document_timeout = _env_float_or_none("DOCLING_DOCUMENT_TIMEOUT_SECONDS")

    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )


@lru_cache(maxsize=2)
def _shared_converter(force_full_page_ocr: bool):
    """Converters are expensive to build (layout, table and OCR models load
    here), so the two variants are built at most once per process."""
    return create_docling_converter(force_full_page_ocr=force_full_page_ocr)


async def extract_text_with_docling(file_name: str, file_bytes: bytes, converter=None) -> str:
    result = await extract_segments_with_docling(
        file_name=file_name,
        file_bytes=file_bytes,
        converter=converter,
    )
    return "\n\n".join(segment.text for segment in result.segments if segment.text.strip())


async def extract_segments_with_docling(
    file_name: str,
    file_bytes: bytes,
    converter=None,
) -> ExtractionResult:
    """Return ordered segments for the document, split by page and section.

    Falls back to a single unstructured segment if neither page provenance
    nor headings are available for the source format.
    """
    suffix = Path(file_name).suffix or ".bin"

    with tempfile.TemporaryDirectory(prefix="docling-") as tmp_dir:
        input_path = Path(tmp_dir) / f"input{suffix}"
        input_path.write_bytes(file_bytes)

        active_converter = converter if converter is not None else _shared_converter(False)
        conversion = active_converter.convert(str(input_path))
        segments = _segments_from_result(conversion)

        result = ExtractionResult(
            segments=segments,
            warning=_conversion_warning(conversion, file_name),
        )

        # An explicitly supplied converter is the caller's choice; don't
        # second-guess it with a retry on a different configuration.
        if converter is None and _text_layer_looks_empty(conversion, segments, file_name):
            result = _retry_with_full_page_ocr(
                input_path=input_path,
                file_name=file_name,
                first_pass=result,
            )

        if not result.segments:
            raise RuntimeError("docling did not produce parseable text output")
        return result


def _retry_with_full_page_ocr(
    input_path: Path,
    file_name: str,
    first_pass: ExtractionResult,
) -> ExtractionResult:
    """Re-convert with full-page OCR and keep whichever pass found more text."""
    logger.info("Retrying %s with full-page OCR: first pass produced little text", file_name)
    try:
        conversion = _shared_converter(True).convert(str(input_path))
        segments = _segments_from_result(conversion)
    except Exception as exc:
        logger.warning("Full-page OCR retry failed for %s: %s", file_name, exc)
        return first_pass

    if _total_chars(segments) <= _total_chars(first_pass.segments):
        logger.info("Full-page OCR retry for %s found no extra text; keeping first pass", file_name)
        return first_pass

    logger.info(
        "Full-page OCR retry for %s recovered %s characters (first pass: %s)",
        file_name,
        _total_chars(segments),
        _total_chars(first_pass.segments),
    )
    return ExtractionResult(
        segments=segments,
        warning=_conversion_warning(conversion, file_name),
        used_full_page_ocr=True,
    )


def _conversion_warning(result: object, file_name: str) -> str | None:
    """Describe an incomplete conversion, or None when docling parsed it all.

    Docling reports PARTIAL_SUCCESS — pages dropped from the document because
    the backend could not parse them, or because a document timeout fired —
    without raising, even with `raises_on_error=True`. Left unchecked, a
    truncated document is indistinguishable from a complete one.
    """
    status = getattr(result, "status", None)
    status_value = str(getattr(status, "value", status) or "").lower()
    if status_value not in {"partial_success", "failure"}:
        return None

    errors = getattr(result, "errors", None) or []
    details = "; ".join(
        str(getattr(err, "error_message", err)) for err in errors[:10]
    )
    if len(errors) > 10:
        details += f" (+{len(errors) - 10} more)"

    warning = (
        f"docling reported {status_value} for {file_name}: the extracted text is incomplete."
        + (f" {details}" if details else "")
    )
    logger.warning(warning)
    return warning


def _text_layer_looks_empty(result: object, segments: list[DocumentSegment], file_name: str) -> bool:
    """True when a paginated document yielded implausibly little text."""
    document = getattr(result, "document", None)
    if document is None:
        return False

    num_pages = getattr(document, "num_pages", None)
    if not callable(num_pages):
        return False
    try:
        pages = num_pages()
    except Exception:
        return False

    # Non-paginated formats (docx, html, md) report 0 pages and have no OCR
    # pipeline to retry with.
    if not pages:
        return False

    threshold = _env_int("DOCLING_MIN_CHARS_PER_PAGE", _DEFAULT_MIN_CHARS_PER_PAGE)
    chars_per_page = _total_chars(segments) / pages
    if chars_per_page >= threshold:
        return False

    logger.info(
        "%s averaged %.1f chars/page across %s pages (threshold %s)",
        file_name,
        chars_per_page,
        pages,
        threshold,
    )
    return True


def _total_chars(segments: list[DocumentSegment]) -> int:
    return sum(len(segment.text) for segment in segments)


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
            label = _item_label(item)
            text = _item_text(item, document, label)
            if not text:
                continue

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


def _item_text(item: object, document: object, label: str) -> str:
    """The text one document item contributes to the extraction.

    Most items carry their content in `text`, but a table has no `text`
    attribute at all — its content lives in the cell grid — so it has to be
    rendered explicitly. Pictures are skipped: their `export_to_markdown`
    returns an image placeholder, not their contents.
    """
    if label == _TABLE_LABEL:
        return _table_markdown(item, document)

    text = getattr(item, "text", None)
    if isinstance(text, str) and text.strip():
        return text
    return ""


def _table_markdown(item: object, document: object) -> str:
    """Render a table's cell grid as a markdown table.

    The caption is stripped off: docling includes it in the export, but
    iterate_items also yields it as its own caption item, so leaving it here
    would embed it twice.
    """
    exporter = getattr(item, "export_to_markdown", None)
    if not callable(exporter):
        return ""

    try:
        rendered = exporter(document)
    except TypeError:
        # Older docling-core takes no document argument (and omits the caption).
        rendered = exporter()
    except Exception as exc:
        logger.warning("Could not render table as markdown: %s", exc)
        return ""

    if not isinstance(rendered, str) or not rendered.strip():
        return ""

    caption_text = getattr(item, "caption_text", None)
    if callable(caption_text):
        try:
            caption = caption_text(document)
        except Exception:
            caption = ""
        if caption and rendered.startswith(caption):
            rendered = rendered[len(caption):].lstrip("\n")

    return rendered


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


def fail_on_partial_extraction() -> bool:
    """Whether an incomplete extraction should fail the file outright.

    Off by default: partial text is still worth indexing, and the warning is
    recorded either way. Set DOCLING_FAIL_ON_PARTIAL=true to reject instead.
    """
    return _env_flag("DOCLING_FAIL_ON_PARTIAL")
