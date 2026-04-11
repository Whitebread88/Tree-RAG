"""Pre-download docling and OCR model weights during Docker build.

Downloads all ML model files so they are cached in the Docker image layer.
Avoids running actual inference (which requires heavy CPU for JIT compilation
and would time out on Cloud Build VMs).
"""

print("Downloading docling models...")

# 1. Instantiate DocumentConverter — triggers HuggingFace model downloads
#    for layout analysis and table structure recognition.
from docling.document_converter import DocumentConverter
converter = DocumentConverter()
del converter
print("Docling converter models downloaded.")

# 2. Initialize RapidOCR — triggers OCR model downloads from ModelScope.
try:
    from rapidocr import RapidOCR
    engine = RapidOCR()
    del engine
    print("RapidOCR models downloaded.")
except Exception as exc:
    print(f"RapidOCR pre-download skipped: {exc}")

print("Model pre-download complete.")
