"""Pre-download and initialize docling model weights during Docker build.

Builds the same converters the job uses at runtime (via
docling_service.create_docling_converter) and converts a minimal PDF to
trigger downloads AND initialization of all ML models (layout, table
structure, OCR). Running this on a high-CPU Cloud Build machine bakes the
fully initialized models into the Docker image so the Cloud Run job starts
with zero model setup overhead.

Failures are fatal. Docling only builds its pipeline for a document it
considers valid, so a swallowed error here produces an image that looks
warmed but downloads every model on the first real file instead.
"""
import sys
import tempfile
from pathlib import Path

from docling_service import create_docling_converter

# Minimal valid PDF — just enough to trigger pipeline initialization
# and all model downloads (layout, table structure, OCR).
MINIMAL_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 72 72]/Parent 2 0 R/Resources<<>>>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
    b"0000000058 00000 n \n0000000115 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n206\n%%EOF"
)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "dummy.pdf"
        pdf_path.write_bytes(MINIMAL_PDF)

        # Both variants are warmed: the job falls back to the full-page-OCR
        # converter for PDFs whose text layer comes back empty.
        for force_full_page_ocr in (False, True):
            print(f"Initializing docling models (force_full_page_ocr={force_full_page_ocr})...")
            try:
                converter = create_docling_converter(force_full_page_ocr=force_full_page_ocr)
                result = converter.convert(str(pdf_path))
            except Exception as exc:
                print(f"ERROR: docling model warm-up failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 1
            print(f"  converted warm-up document with status={getattr(result, 'status', 'unknown')}")

    print("Docling model pre-download complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
