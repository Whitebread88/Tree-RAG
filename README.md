# Tree-RAG

## Dependency profiles

This project now has separate dependency sets:

- `requirements-service.txt`: Cloud Run service dependencies (upload/chat/DB + Cloud Run job trigger).
- `requirements-job.txt`: job dependencies (includes everything in `requirements-service.txt` plus `raganything[all]`).

### Local setup (Linux/macOS)

1. Install Cloud Run service dependencies:

```bash
pip install -r requirements-service.txt
```

2. Install job-only dependencies when running file processing locally:

```bash
pip install -r requirements-job.txt
```

3. Install LibreOffice (needed for job processing formats):

- Ubuntu/Debian:

```bash
sudo apt-get update && sudo apt-get install -y --no-install-recommends libreoffice-writer
```

- macOS (Homebrew):

```bash
brew install --cask libreoffice
```

4. Optional (if PaddleOCR parser is desired and not already resolved by extras):

```bash
pip install "raganything[paddleocr]"
```

### Docker setup

The `Dockerfile` supports selecting service or job dependencies using `REQUIREMENTS_FILE`.

```bash
# Service image (without raganything)
docker build -t tree-rag-service --build-arg REQUIREMENTS_FILE=requirements-service.txt .

# Job image (includes raganything)
docker build -t tree-rag-job --build-arg REQUIREMENTS_FILE=requirements-job.txt .
```

## Cloud Run service + Cloud Run job flow

- The `/files/upload` API now records uploaded files with a `processing_status` field set to `uploaded`.
- After a successful upload request (single or batch), the service triggers a Cloud Run Job execution.
- The Cloud Run Job should use the same image and run:

```bash
python job_runner.py
```

- `job_runner.py` processes all files currently in `uploaded` status and transitions each file to:
  - `processing` while it is being chunked/embedded
  - `completed` when done successfully

Required service environment variables for job triggering:

- `GOOGLE_CLOUD_PROJECT` (or `PROJECT_ID`)
- `CLOUD_RUN_JOB_REGION` (for example, `us-central1`)
- `CLOUD_RUN_JOB_NAME` (job name or full `projects/.../locations/.../jobs/...` resource path)

### Quick verification

```bash
python -c "import raganything; print('raganything import: OK')"
libreoffice --version  # provided by libreoffice-writer package
```
