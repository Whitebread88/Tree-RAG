import os

import google.generativeai as genai


def embed_chunks(chunks: list[str]) -> list[list[float]]:
    if not chunks:
        return []

    model_name = os.getenv("EMBEDDING_MODEL", "models/text-embedding-004")
    configured_dim = int(os.getenv("EMBEDDING_DIM", "768"))

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY environment variable is required for embeddings")

    genai.configure(api_key=api_key)

    vectors: list[list[float]] = []
    for chunk in chunks:
        response = genai.embed_content(
            model=model_name,
            content=chunk,
            task_type="retrieval_document",
            output_dimensionality=configured_dim,
        )
        embedding = response["embedding"]
        vectors.append(embedding)

    return vectors
