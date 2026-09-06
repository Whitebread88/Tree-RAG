# Tree-RAG

## Dependency profiles

This project now has separate dependency sets:

- `requirements-service.txt`: Cloud Run service dependencies (upload/chat/DB + Cloud Run job trigger).
- `requirements-job.txt`: job dependencies (includes everything in `requirements-service.txt` plus `docling`).

### Local setup (Linux/macOS)

1. Install Cloud Run service dependencies:

```bash
pip install -r requirements-service.txt
```

2. Install job-only dependencies when running file processing locally:

```bash
pip install -r requirements-job.txt
```

3. Install dev dependencies to run the tests:

```bash
pip install -r requirements-dev.txt
pytest tests/
```

### Supported input formats

Extraction is handled by docling. It accepts PDF, DOCX, PPTX, XLSX, HTML,
Markdown, CSV, AsciiDoc and images (PNG/JPEG/TIFF/BMP/WEBP).

Legacy binary Office formats (`.doc`, `.xls`, `.ppt`) are **not** supported and
will fail with an unsupported-format error. Converting them to their modern
equivalents before upload is the supported path.

### Extraction tuning

Defaults are set explicitly in `docling_service.create_docling_converter` — OCR
engine included, because docling's own default picks an engine by probing which
optional dependencies happen to be installed, and silently does no OCR at all
when it finds none. These environment variables adjust the job's behaviour:

| Variable | Default | Effect |
| --- | --- | --- |
| `DOCLING_MIN_CHARS_PER_PAGE` | `64` | Below this many characters per page, the PDF is re-converted with full-page OCR. Catches scanned pages and broken text layers, which docling otherwise returns as empty without an error. |
| `DOCLING_DOCUMENT_TIMEOUT_SECONDS` | unset (no timeout) | Aborts conversion after this long. Docling then returns whatever it finished, so the file is recorded with a warning. |
| `DOCLING_FAIL_ON_PARTIAL` | `false` | When true, a partially-parsed document fails the file instead of being indexed with a warning. |

When docling cannot parse a whole document it reports `PARTIAL_SUCCESS`
*without* raising. The job records that on `uploaded_files.processing_warning`
and returns it as `warning` on the file's processing result, so a truncated
extraction is not indistinguishable from a clean one.


### Docker setup

The `Dockerfile` supports selecting service or job dependencies using `REQUIREMENTS_FILE`.

```bash
# Service image (without docling)
docker build -t tree-rag-service --build-arg REQUIREMENTS_FILE=requirements-service.txt .

# Job image (includes docling)
docker build -t tree-rag-job --build-arg REQUIREMENTS_FILE=requirements-job.txt .
```

### Cloud Build for separate service and job images

Use the included `cloudbuild.yaml` to build two independent Artifact Registry images from the same repo:

- Service image: built with `requirements-service.txt`
- Job image: built with `requirements-job.txt`

The Cloud Build config passes `--build-arg REQUIREMENTS_FILE=...`, which is supported by the repository `Dockerfile` via `ARG REQUIREMENTS_FILE` and conditional package install logic.

```bash
gcloud builds submit \
  --config cloudbuild.yaml \
  --substitutions _REGION=asia-southeast1,_AR_REPO=tree-rag,_SERVICE_NAME=tree-rag-service,_JOB_NAME=tree-rag-job
```

By default, the build only creates/pushes images. To also deploy the Cloud Run service and update the Cloud Run job in the same pipeline:

```bash
gcloud builds submit \
  --config cloudbuild.yaml \
  --substitutions _REGION=asia-southeast1,_AR_REPO=tree-rag,_SERVICE_NAME=tree-rag-service,_JOB_NAME=tree-rag-job,_DEPLOY_SERVICE=true,_DEPLOY_JOB=true
```

This keeps the service image lightweight while the job image can include heavy parsing dependencies.

## Retrieval tuning

Chat retrieval runs in two stages: pgvector returns the `RAG_TOP_K` nearest
chunks by cosine distance, then thresholding, dedup and a context budget cut
that set down before it reaches the model. `top_k` is a candidate budget, not a
result count — expect noticeably fewer chunks in the prompt than were fetched.

| Variable | Default | Effect |
| --- | --- | --- |
| `RAG_TOP_K` | `50` | Nearest-neighbour candidates fetched per query. Raise this to improve recall; the threshold cannot surface a chunk that was never fetched. Capped at 100 by the API schema. |
| `RAG_SIMILARITY_THRESHOLD` | `0.5` | Cosine-similarity floor. Chunks below it never reach the model. |
| `RAG_DEDUP_OVERLAP_RATIO` | `0.5` | Two chunks from one file overlapping by this fraction of the shorter one count as the same passage. `1.0` disables dedup. |
| `RAG_MAX_CONTEXT_CHARS` | `100000` | Soft cap on retrieved context handed to the model. The top-scoring chunk is always kept. |
| `CHAT_HISTORY_MESSAGES` | `6` | Prior messages fed to the model as conversation context. |
| `RAG_QUERY_REWRITE` | `1` | Rewrite follow-ups into standalone queries before embedding. |

Both retrieval settings can also be overridden per request via `top_k` and
`similarity_threshold` on the chat endpoint; omit them to use the server
defaults above.

The threshold is calibrated for `gemini-embedding-001` at 768 dimensions with
`RETRIEVAL_QUERY`/`RETRIEVAL_DOCUMENT` task types, where unrelated text still
scores roughly 0.3-0.45 — the embedding space is not centred on zero, so a
lower floor admits nearly everything. Every query logs a `Retrieval funnel:`
line with the candidate count, how many each stage dropped, and the best score
seen, so a thin or empty answer can be traced to the setting responsible. A
query where nothing clears the threshold also logs a warning naming the best
candidate's score.

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

  A file that completes but could only be parsed in part stays `completed` and
  carries `processing_warning`; its processing result comes back with status
  `processed_with_warnings`.

Required service environment variables for job triggering:

- `GOOGLE_CLOUD_PROJECT` (or `PROJECT_ID`)
- `CLOUD_RUN_JOB_REGION` (for example, `us-central1`)
- `CLOUD_RUN_JOB_NAME` (job name or full `projects/.../locations/.../jobs/...` resource path)

### Quick verification

```bash
python -c "from docling.document_converter import DocumentConverter; print('docling import: OK')"
python -c "from docling_service import create_docling_converter; create_docling_converter(); print('docling pipeline: OK')"
```

### Troubleshooting "job starts but no file is processed"

If Cloud Run Job executions start but files remain unprocessed, check these first:

1. **Job entrypoint/command**
   - Ensure the Cloud Run Job is configured with command `python` and args `job_runner.py` (or equivalent).
   - The image default command runs `uvicorn` for the API service, so the job command should be explicitly set in Cloud Run Job config.

2. **Job image dependency profile**
   - Build the job image with `--build-arg REQUIREMENTS_FILE=requirements-job.txt`.
   - The service profile does not include `docling`, so parsing will fail in job runs.

3. **Environment parity between service and job**
   - Confirm these are set on the **job** (not only on the service):
     - `INSTANCE_CONNECTION_NAME`, `DB_USER`, `DB_NAME`
     - `GCS_BUCKET_NAME`
     - `GEMINI_API_KEY` (for embeddings and chat)

4. **Database status/error inspection**
   - Files that fail processing are now marked `failed` and include `processing_error` so failures are visible and don't look "stuck".
