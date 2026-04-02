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
  - `failed` when an error occurs (with `processing_error` set for troubleshooting)

Required service environment variables for job triggering:

- `GOOGLE_CLOUD_PROJECT` (or `PROJECT_ID`)
- `CLOUD_RUN_JOB_REGION` (for example, `us-central1`)
- `CLOUD_RUN_JOB_NAME` (job name or full `projects/.../locations/.../jobs/...` resource path)

### Quick verification

```bash
python -c "import raganything; print('raganything import: OK')"
libreoffice --version  # provided by libreoffice-writer package
```

### Troubleshooting "job starts but no file is processed"

If Cloud Run Job executions start but files remain unprocessed, check these first:

1. **Job entrypoint/command**
   - Ensure the Cloud Run Job command is `python job_runner.py`.
   - If you leave command unset, the Docker default command runs `uvicorn ...` (API server), which will not process uploaded files.

2. **Job image dependency profile**
   - Build the job image with `--build-arg REQUIREMENTS_FILE=requirements-job.txt`.
   - The service profile does not include `raganything`, so parsing will fail in job runs.

3. **Environment parity between service and job**
   - Confirm these are set on the **job** (not only on the service):
     - `INSTANCE_CONNECTION_NAME`, `DB_USER`, `DB_NAME`
     - `GCS_BUCKET_NAME`
     - `GOOGLE_API_KEY` (for embeddings)

4. **Database status/error inspection**
   - Files that fail processing are now marked `failed` and include `processing_error` so failures are visible and don't look "stuck".
