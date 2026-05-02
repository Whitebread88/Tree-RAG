import logging
import os
from functools import lru_cache

from google import genai
from sqlmodel import Session, select

from db import engine
from embeddings_service import embed_query
from models import FileChunkEmbedding, UploadedFile
from schemas import ChatQueryRequest, ChatQueryResponse, RetrievedContextChunk

logger = logging.getLogger(__name__)

# Default similarity floor; chunks below this are dropped from the LLM prompt
# so they can't poison answers with irrelevant context. Tune empirically.
_DEFAULT_SIMILARITY_THRESHOLD = float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.3"))

# Soft cap on the total characters of retrieved context handed to the LLM.
# Gemini Flash has a huge context window, but more context here means more
# noise dilution, not better answers.
_MAX_CONTEXT_CHARS = int(os.getenv("RAG_MAX_CONTEXT_CHARS", "12000"))

_NO_CONTEXT_ANSWER = "I could not find relevant processed chunks for this query."

_SYSTEM_INSTRUCTION = (
    "You are a retrieval-augmented assistant. Answer ONLY using the provided context. "
    "If the context does not contain enough information, say so explicitly and name what is missing. "
    "Cite the sources you used with the [n] markers from the context. "
    "Keep answers concise and factual."
)


@lru_cache(maxsize=1)
def _get_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is required for chatbot responses")
    return genai.Client(api_key=api_key)


def answer_query(request: ChatQueryRequest) -> ChatQueryResponse:
    query_embedding = embed_query(request.query)
    threshold = request.similarity_threshold if request.similarity_threshold is not None else _DEFAULT_SIMILARITY_THRESHOLD

    with Session(engine) as session:
        distance = FileChunkEmbedding.embedding.cosine_distance(query_embedding).label("distance")
        statement = (
            select(FileChunkEmbedding, UploadedFile, distance)
            .join(UploadedFile, UploadedFile.id == FileChunkEmbedding.uploaded_file_id)
            .order_by(distance.asc())
            .limit(request.top_k)
        )

        if request.folder_names:
            statement = statement.where(UploadedFile.folder_name.in_(request.folder_names))

        rows = session.exec(statement).all()

    context_chunks, prompt_blocks = _build_context(rows=rows, threshold=threshold)

    if not context_chunks:
        return ChatQueryResponse(
            query=request.query,
            answer=_NO_CONTEXT_ANSWER,
            context=[],
        )

    answer = _generate_answer(query=request.query, context_blocks=prompt_blocks)

    return ChatQueryResponse(
        query=request.query,
        answer=answer,
        context=context_chunks,
    )


def _build_context(
    rows,
    threshold: float,
) -> tuple[list[RetrievedContextChunk], list[str]]:
    context_chunks: list[RetrievedContextChunk] = []
    prompt_blocks: list[str] = []
    used_chars = 0

    for row in rows:
        chunk, uploaded_file, distance = row
        similarity = max(0.0, 1 - float(distance))
        if similarity < threshold:
            continue

        block = _format_prompt_block(
            rank=len(prompt_blocks) + 1,
            file_name=uploaded_file.original_file_name,
            page_number=chunk.page_number,
            chunk_index=chunk.chunk_index,
            chunk_text=chunk.chunk_text,
        )
        # Stop if adding this block would blow the context budget — but always
        # keep the first chunk so the LLM has something to reason over.
        if prompt_blocks and used_chars + len(block) > _MAX_CONTEXT_CHARS:
            break

        context_chunks.append(
            RetrievedContextChunk(
                file_id=uploaded_file.id,
                file_name=uploaded_file.original_file_name,
                folder_name=uploaded_file.folder_name,
                chunk_index=chunk.chunk_index,
                chunk_text=chunk.chunk_text,
                similarity_score=similarity,
                page_number=chunk.page_number,
            )
        )
        prompt_blocks.append(block)
        used_chars += len(block)

    return context_chunks, prompt_blocks


def _format_prompt_block(
    rank: int,
    file_name: str,
    page_number: int | None,
    chunk_index: int,
    chunk_text: str,
) -> str:
    location = f"page {page_number}" if page_number is not None else f"chunk {chunk_index}"
    return f"[{rank}] {file_name} ({location})\n{chunk_text}"


def _generate_answer(query: str, context_blocks: list[str]) -> str:
    client = _get_client()
    model_name = os.getenv("CHAT_MODEL", "gemini-2.0-flash")

    prompt = (
        f"{_SYSTEM_INSTRUCTION}\n\n"
        f"Question: {query}\n\n"
        "Context:\n"
        + "\n\n".join(context_blocks)
        + "\n\nAnswer:"
    )

    response = client.models.generate_content(model=model_name, contents=prompt)
    return response.text or "I found relevant context but could not generate a response."
