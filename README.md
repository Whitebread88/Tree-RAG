# Tree-RAG

**Chat with your own documents.** Upload PDFs, Word files, slide decks,
spreadsheets or scanned pages, then ask questions about them in plain language.
Every answer is built only from passages retrieved from your files, and it
names the files it used.

This repository is the retrieval backend: document ingestion, vector search and
grounded answer generation, running as a FastAPI service and a background job
on Google Cloud Run. The chat interface is a separate project.

**[Live demo](https://chatbot-ui-zeta-eight-78.vercel.app/chat)** ·
**[Frontend repo (chatbot-ui)](https://github.com/bthk2151/chatbot-ui)** ·
**[Backend repo (Tree-RAG)](https://github.com/Whitebread88/Tree-RAG)**

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/cover/rag-cover-dark.svg">
  <img alt="How the RAG chat works. When a file is uploaded it is extracted with Docling, split into passages that stop at page and section breaks, embedded and stored in Postgres with pgvector. When someone asks a question, a follow-up is first rewritten into a standalone query, embedded, matched against the 50 closest passages, and answered by Gemini from those passages alone." src="docs/cover/rag-cover-light.svg">
</picture>

## What it does

- **Upload in batches.** Drop files into a named folder. Processing runs in the
  background, and each folder reports its progress.
- **Ask across your library.** Search one folder, several, or everything you
  have uploaded.
- **Hold a conversation.** Follow-ups such as "what about annual plans?" are
  understood in the context of what was asked before.
- **Trust the answer.** Replies list their source files, and say so plainly
  when the documents do not contain the answer instead of guessing.
- **Pick up where you left off.** Conversations are saved, and each user only
  ever sees their own files and chats.

## How it fits together

| Component | Runs on | Role |
| --- | --- | --- |
| [chatbot-ui](https://github.com/bthk2151/chatbot-ui) | Vercel | Sign-in, uploads and the chat interface. Its server signs the short-lived tokens this API accepts. |
| API service (`main.py`) | Cloud Run | FastAPI app for uploads, processing status, chat and conversation history. Deliberately light: no ML dependencies. |
| Processing job (`job_runner.py`) | Cloud Run Jobs | Started after each upload. Extracts, chunks and embeds every new file. Docling's models are baked into its image. |
| File storage | Cloud Storage | The original uploaded files. |
| Database | PostgreSQL + pgvector | Files, passages and their 768-dimension vectors (HNSW index), users and conversations. |
| Models | Gemini API | `gemini-embedding-001` for embeddings, a Gemini Flash model for query rewriting and answers. |

## Engineering highlights

**Extraction that doesn't fail silently.** Docling reads layout, reading order
and tables, and tables are rendered to Markdown so their cell contents are not
dropped. When a PDF yields fewer than 64 characters per page, which usually
means a scan or a broken text layer, it is re-run with full-page OCR and the
pass that found more text wins. If Docling can only parse part of a document,
the file is still indexed but carries a visible warning, so a truncated
extraction can't pass for a clean one.

**Passages that know where they came from.** Text is split into roughly
1,000-character passages with a 200-character overlap, and a passage never
crosses a page or section break. Before embedding, each one is prefixed with a
breadcrumb such as `Policies > refunds.pdf > Annual Plans > page 4`. A passage
that only says "the limit is 30 days" will then still match a question about
refund windows.

**A retrieval funnel instead of a fixed top-k.** pgvector returns the 50
nearest candidates by cosine distance, then a similarity floor, duplicate
removal and a context budget cut that set down. The 0.5 floor is calibrated for
`gemini-embedding-001`, where unrelated text still scores around 0.3 to 0.45.
Overlapping neighbours from the same file are detected by their stored
character offsets, so the model sees distinct facts rather than the same
paragraph three times. Each query logs how many candidates every stage dropped,
which makes a thin answer traceable to the setting responsible.

**Follow-ups are rewritten before they are searched.** Mid-conversation, the
latest message is rewritten into a standalone query before it is embedded, so
retrieval works on the real topic rather than on "what about that?". If the
rewrite fails or comes back unusable, the original wording is used: a slightly
worse search beats a failed request.

**Citations the model can't invent.** The model cites numbered context blocks,
because numbers are easier to reproduce exactly than file names. The service
strips those markers out of the prose and appends a single, de-duplicated
`Sources:` line. A marker pointing at a block that was never retrieved is
removed without crediting any file.

**Ingestion that recovers on its own.** Each file moves through `uploaded`,
`processing`, then `completed` or `failed`, with the error recorded on failure.
A file left mid-processing by a crashed job goes back in the queue on the next
start. Re-uploading identical content (matched by SHA-256) copies the existing
embeddings instead of paying for extraction and embedding a second time.

**No idle database connections during model calls.** Answering a question
takes up to three Gemini round trips. Each database phase runs in its own
short session, so model latency never holds a slot in the small connection
pool and slow answers can't starve unrelated requests.

**Locked-down by default.** The API only accepts RS256 tokens signed by the
chat UI's server, with issuer and audience checked strictly and a maximum
lifetime of five minutes. Every upload, search and conversation lookup is
scoped to the authenticated user, and a request that names any other user ID
is rejected.

**Two images from one Dockerfile.** The API image installs only what the API
needs. The job image adds Docling and downloads its layout, table and OCR
model weights at build time, so a job starts processing without fetching
models first. Cloud Build builds and deploys both in parallel.

## Tech stack

| Area | Tools |
| --- | --- |
| API | Python 3.11, FastAPI, SQLModel, SQLAlchemy |
| Document processing | Docling, RapidOCR (onnxruntime) |
| Embeddings and generation | Google Gemini via `google-genai` |
| Vector search | PostgreSQL, pgvector (HNSW, cosine distance) |
| Infrastructure | Cloud Run (service and job), Cloud Storage, Cloud Build, Artifact Registry, Secret Manager |
| Auth | PyJWT (RS256) |
| Tests | pytest |
| Frontend | [chatbot-ui](https://github.com/bthk2151/chatbot-ui), deployed on Vercel |

## API

Every route except `/` and `/health` requires a bearer token. Interactive
OpenAPI docs are served at `/docs`.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/users/login` | Record the signed-in user. Call once before uploading. |
| `POST` | `/files/upload` | Upload one or more files into a folder and start processing. |
| `GET` | `/files/processing-status` | Processing state of a folder. |
| `POST` | `/chat/query` | Ask a question, optionally continuing a conversation or limiting the search to certain folders. |
| `GET` | `/chat/conversations` | List the user's conversations. |
| `GET` | `/chat/conversations/{id}` | Messages and source files for one conversation. |
| `DELETE` | `/chat/conversations/{id}` | Delete a conversation. |

A chat response carries the answer, the query that was actually searched
(which differs from the question when a follow-up was rewritten), and every
retrieved passage with its file, page and similarity score.

## Project layout

```text
main.py                  FastAPI app and routes
auth.py                  JWT verification
upload_service.py        Store uploads in Cloud Storage, record them, start the job
job_runner.py            Cloud Run Job entry point
processing_service.py    Per file: extract, chunk, embed, store
docling_service.py       Docling setup, OCR fallback, table rendering
chunking.py              Page- and section-aware chunker
embeddings_service.py    Gemini embeddings with retry and normalisation
chatbot_service.py       Query rewrite, retrieval funnel, answer and citations
db_init.py               Schema creation and idempotent migrations
docs/cover/              Cover infographic and the script that generates it
tests/                   Retrieval and extraction tests
```

## Running it locally

You need Python 3.11, a PostgreSQL database where the `vector` extension can be
created, a Gemini API key, a Cloud Storage bucket with application default
credentials, and the RSA public key that matches the chat UI's signing key.

```bash
pip install -r requirements-dev.txt   # service, job and test dependencies
pytest tests/                         # no database or API key needed
```

Start the API (the schema is created on startup):

```bash
export DATABASE_URL="postgresql://user:pass@localhost:5432/treerag"
export GEMINI_API_KEY="..."
export GCS_BUCKET_NAME="..."
export RAG_JWT_PUBLIC_KEY="$(cat public.pem)"
export RAG_JWT_ISSUER="..."
export RAG_JWT_AUDIENCE="..."
uvicorn main:app --reload --port 8080
```

Uploads also start a Cloud Run Job, so they need the job variables listed under
[Configuration](#configuration). To process queued files on your own machine
instead, run `python job_runner.py` with the same database, bucket and Gemini
settings.

## Configuration

<details>
<summary><strong>Required environment variables</strong></summary>

| Variable | Used by | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | service, job | PostgreSQL connection string. |
| `GEMINI_API_KEY` | service, job | Embeddings and chat. |
| `GCS_BUCKET_NAME` | service, job | Bucket that holds uploaded files. |
| `RAG_JWT_PUBLIC_KEY` | service | PEM-encoded RSA public key, at least 2048 bits. Escaped `\n` newlines are accepted. |
| `RAG_JWT_ISSUER` | service | Expected `iss` claim. |
| `RAG_JWT_AUDIENCE` | service | Expected `aud` claim. |
| `GOOGLE_CLOUD_PROJECT` (or `PROJECT_ID`) | service | Project of the processing job. |
| `CLOUD_RUN_JOB_REGION` | service | Region of the processing job, for example `asia-southeast1`. |
| `CLOUD_RUN_JOB_NAME` | service | Job name, or a full `projects/.../locations/.../jobs/...` path. |

Model choices can be overridden with `CHAT_MODEL` (default `gemini-3.7-flash`),
`QUERY_REWRITE_MODEL` (defaults to the chat model) and `EMBEDDING_MODEL`
(default `gemini-embedding-001`). Vectors are stored at 768 dimensions, so a
different embedding model means re-embedding every file and recalibrating the
similarity floor.

</details>

<details>
<summary><strong>Retrieval tuning</strong></summary>

Chat retrieval runs in two stages: pgvector returns the `RAG_TOP_K` nearest
chunks by cosine distance, then thresholding, dedup and a context budget cut
that set down before it reaches the model. `top_k` is a candidate budget, not a
result count, so expect noticeably fewer chunks in the prompt than were fetched.

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
scores roughly 0.3-0.45. The embedding space is not centred on zero, so a
lower floor admits nearly everything. Every query logs a `Retrieval funnel:`
line with the candidate count, how many each stage dropped, and the best score
seen, so a thin or empty answer can be traced to the setting responsible. A
query where nothing clears the threshold also logs a warning naming the best
candidate's score.

</details>

<details>
<summary><strong>Supported formats and extraction tuning</strong></summary>

Docling accepts PDF, DOCX, PPTX, XLSX, HTML, Markdown, CSV, AsciiDoc and images
(PNG, JPEG, TIFF, BMP, WEBP). Legacy binary Office formats (`.doc`, `.xls`,
`.ppt`) are **not** supported and fail with an unsupported-format error;
convert them to their modern equivalents before uploading.

Defaults are set explicitly in `docling_service.create_docling_converter`, OCR
engine included, because Docling's own default picks an engine by probing which
optional dependencies happen to be installed, and silently does no OCR at all
when it finds none. These variables adjust the job's behaviour:

| Variable | Default | Effect |
| --- | --- | --- |
| `DOCLING_MIN_CHARS_PER_PAGE` | `64` | Below this many characters per page, the PDF is re-converted with full-page OCR. Catches scanned pages and broken text layers, which Docling otherwise returns as empty without an error. |
| `DOCLING_DOCUMENT_TIMEOUT_SECONDS` | unset (no timeout) | Aborts conversion after this long. Docling then returns whatever it finished, so the file is recorded with a warning. |
| `DOCLING_FAIL_ON_PARTIAL` | `false` | When true, a partially parsed document fails the file instead of being indexed with a warning. |

When Docling cannot parse a whole document it reports `PARTIAL_SUCCESS`
without raising. The job records that on `uploaded_files.processing_warning`
and returns it as `warning` on the file's processing result, with status
`processed_with_warnings`.

</details>

## Deployment

<details>
<summary><strong>Docker images and Cloud Build</strong></summary>

The `Dockerfile` builds either image depending on `REQUIREMENTS_FILE`:

- `requirements-service.txt`: the API service (upload, chat, database, job trigger).
- `requirements-job.txt`: everything in the service profile plus Docling, with
  model weights downloaded into the image at build time.

```bash
# Service image (no Docling)
docker build -t tree-rag-service --build-arg REQUIREMENTS_FILE=requirements-service.txt .

# Job image (includes Docling and its models)
docker build -t tree-rag-job --build-arg REQUIREMENTS_FILE=requirements-job.txt .
```

`cloudbuild.yaml` builds both images in parallel, pushes them to Artifact
Registry, and deploys the Cloud Run service and job. `DATABASE_URL` is supplied
from Secret Manager at deploy time, and an `HF_TOKEN` secret is read during the
job image build for faster model downloads.

```bash
gcloud builds submit \
  --config cloudbuild.yaml \
  --substitutions _REGION=asia-southeast1,_AR_REPO=tree-rag,_SERVICE_NAME=chatbot-rag,_JOB_NAME=rag-file-process
```

Add `_DEPLOY_SERVICE=false,_DEPLOY_JOB=false` to the substitutions to build and
push the images without deploying them.

The job runs `python job_runner.py`, which Cloud Build sets as the job's
command. The image's default command starts the API instead.

</details>

<details>
<summary><strong>Troubleshooting: the job starts but no file is processed</strong></summary>

1. **Job command.** The Cloud Run Job must run `python job_runner.py`. The
   image's default command runs `uvicorn` for the API, so the job command has
   to be set explicitly in the job configuration.
2. **Job image profile.** Build the job image with
   `--build-arg REQUIREMENTS_FILE=requirements-job.txt`. The service profile
   does not include Docling, so parsing fails in job runs.
3. **Environment parity.** `DATABASE_URL`, `GCS_BUCKET_NAME` and
   `GEMINI_API_KEY` must be set on the job, not only on the service.
4. **Status and errors.** Files that fail processing are marked `failed` with
   `processing_error` set, so failures are visible rather than looking stuck.
   A file that completed but was only partly parsed stays `completed` and
   carries `processing_warning`.

Quick checks inside the job image:

```bash
python -c "from docling.document_converter import DocumentConverter; print('docling import: OK')"
python -c "from docling_service import create_docling_converter; create_docling_converter(); print('docling pipeline: OK')"
```

</details>
