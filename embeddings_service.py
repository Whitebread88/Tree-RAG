import os

from google import genai


def _get_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is required for embeddings")
    return genai.Client(api_key=api_key, http_options={"api_version": "v1"})


def embed_chunks(chunks: list[str]) -> list[list[float]]:
    if not chunks:
        return []

    client = _get_client()
    model_name = os.getenv("EMBEDDING_MODEL", "text-embedding-004")
    configured_dim = int(os.getenv("EMBEDDING_DIM", "768"))

    response = client.models.embed_content(
        model=model_name,
        contents=chunks,
        config={"output_dimensionality": configured_dim},
    )
    return [e.values for e in response.embeddings]


def embed_query(query: str) -> list[float]:
    if not query.strip():
        raise ValueError("Query cannot be empty")

    client = _get_client()
    model_name = os.getenv("EMBEDDING_MODEL", "text-embedding-004")
    configured_dim = int(os.getenv("EMBEDDING_DIM", "768"))

    response = client.models.embed_content(
        model=model_name,
        contents=query,
        config={"output_dimensionality": configured_dim},
    )
    return response.embeddings[0].values
