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
    if converter is None:
        converter = create_docling_converter()

    suffix = Path(file_name).suffix or ".bin"

    with tempfile.TemporaryDirectory(prefix="docling-") as tmp_dir:
        input_path = Path(tmp_dir) / f"input{suffix}"
        input_path.write_bytes(file_bytes)

        result = converter.convert(str(input_path))
        extracted = _extract_text_from_result(result)
        if not extracted:
            raise RuntimeError("docling did not produce parseable text output")
        return extracted


def _extract_text_from_result(result: object) -> str:
    document = getattr(result, "document", None)
    if document is None:
        return ""

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
