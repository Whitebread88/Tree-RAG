"""Pre-download docling model weights during Docker build.

Downloads model artifacts from HuggingFace so they are baked into the
Docker image.  This is *download-only* — we deliberately skip running
inference (DocumentConverter.convert) because the PyTorch model
initialization / JIT compilation is extremely CPU-intensive and
causes Cloud Build timeouts even on E2_HIGHCPU_8 machines.

The Cloud Run job will still need to initialize the models on first
run (~2-3 min), but it won't need to download them (~5-10 min saved).
"""

from huggingface_hub import snapshot_download

MODELS = [
    "ds4sd/docling-models",
]

for repo_id in MODELS:
    print(f"Downloading {repo_id} ...")
    snapshot_download(repo_id)
    print(f"  {repo_id} done.")

# Trigger EasyOCR model download (uses its own cache, not HuggingFace)
try:
    import easyocr
    print("Downloading EasyOCR models ...")
    easyocr.Reader(["en"], gpu=False, download_enabled=True)
    print("  EasyOCR done.")
except Exception as e:
    print(f"  EasyOCR download skipped: {e}")

print("Model pre-download complete.")
