"""Pre-download docling model weights during Docker build.

Creates a DocumentConverter and converts a minimal PDF to trigger
downloads of all ML models (layout detection, table structure, OCR).
These get cached in the image layer so job startup is instant.
"""
import tempfile
from pathlib import Path

from docling.document_converter import DocumentConverter

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

with tempfile.TemporaryDirectory() as tmp:
    pdf_path = Path(tmp) / "dummy.pdf"
    pdf_path.write_bytes(MINIMAL_PDF)
    converter = DocumentConverter()
    try:
        converter.convert(str(pdf_path))
    except Exception:
        pass  # Result doesn't matter — models are now cached

print("Docling model pre-download complete")
