# Tree-RAG

## RAGAnything full-format setup

This project uses `raganything` for file parsing before chunking and embedding.

To maximize format support, this repository now installs:

- `raganything[all]` (Python extras for text/image/office/parser integrations)
- `libreoffice-writer` (minimal system dependency used for Office document conversion paths)

### Local setup (Linux/macOS)

1. Install Python dependencies:

```bash
pip install -r requirements.txt
```

2. Install LibreOffice:

- Ubuntu/Debian:

```bash
sudo apt-get update && sudo apt-get install -y --no-install-recommends libreoffice-writer
```

- macOS (Homebrew):

```bash
brew install --cask libreoffice
```

3. Optional (if PaddleOCR parser is desired and not already resolved by extras):

```bash
pip install "raganything[paddleocr]"
```

### Docker setup

The `Dockerfile` uses `python:3.11-slim-bookworm`, installs `libreoffice-writer`, and then installs Python dependencies in one layer with cleanup, so no extra steps are needed when building the image:

```bash
docker build -t tree-rag .
```

### Quick verification

```bash
python -c "import raganything; print('raganything import: OK')"
libreoffice --version  # provided by libreoffice-writer package
```

