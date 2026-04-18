import logging
import os
import time
from functools import lru_cache

from google import genai


logger = logging.getLogger(__name__)

EMBEDDING_DIM = 768
EMBEDDING_BATCH_SIZE = 100
_MAX_RETRIES = 4
_INITIAL_BACKOFF_SECONDS = 2.0

_DOCUMENT_TASK_TYPE = "RETRIEVAL_DOCUMENT"
_QUERY_TASK_TYPE = "RETRIEVAL_QUERY"


@lru_cache(maxsize=1)
def _get_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is required for embeddings")
    return genai.Client(api_key=api_key)


def _embed_with_retry(contents: list[str], task_type: str) -> list[list[float]]:
    client = _get_client()
    model_name = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
    config = {
        "output_dimensionality": EMBEDDING_DIM,
        "task_type": task_type,
    }

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = client.models.embed_content(
                model=model_name,
                contents=contents,
                config=config,
            )
            return [e.values for e in response.embeddings]
        except Exception as exc:
            last_exc = exc
            if attempt == _MAX_RETRIES - 1:
                break
            backoff = _INITIAL_BACKOFF_SECONDS * (2 ** attempt)
            logger.warning(
                "Gemini embed_content failed (attempt %s/%s, task_type=%s, batch_size=%s): %s. Retrying in %.1fs",
                attempt + 1,
                _MAX_RETRIES,
                task_type,
                len(contents),
                exc,
                backoff,
            )
            time.sleep(backoff)

    assert last_exc is not None
    raise last_exc


def embed_chunks(chunks: list[str]) -> list[list[float]]:
    if not chunks:
        return []

    vectors: list[list[float]] = []
    for start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
        batch = chunks[start : start + EMBEDDING_BATCH_SIZE]
        vectors.extend(_embed_with_retry(batch, task_type=_DOCUMENT_TASK_TYPE))
    return vectors


def embed_query(query: str) -> list[float]:
    if not query.strip():
        raise ValueError("Query cannot be empty")
    result = _embed_with_retry([query], task_type=_QUERY_TASK_TYPE)
    return result[0]
