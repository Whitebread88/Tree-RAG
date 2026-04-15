import os

from google import genai
from sqlmodel import Session, select

from db import engine
from embeddings_service import embed_query
from models import FileChunkEmbedding, UploadedFile
from schemas import ChatQueryRequest, ChatQueryResponse, RetrievedContextChunk


def answer_query(request: ChatQueryRequest) -> ChatQueryResponse:
    query_embedding = embed_query(request.query)

    with Session(engine) as session:
        distance = FileChunkEmbedding.embedding.cosine_distance(query_embedding).label("distance")
        statement = (
            select(FileChunkEmbedding, UploadedFile, distance)
            .join(UploadedFile, UploadedFile.id == FileChunkEmbedding.uploaded_file_id)
            .order_by(distance.asc())
            .limit(request.top_k)
        )

        if request.folder_name:
            statement = statement.where(UploadedFile.folder_name == request.folder_name)

        rows = session.exec(statement).all()

    if not rows:
        return ChatQueryResponse(
            query=request.query,
            answer="I could not find relevant processed chunks for this query.",
            context=[],
        )

    context_chunks: list[RetrievedContextChunk] = []
    prompt_context_lines: list[str] = []

    for rank, row in enumerate(rows, start=1):
        chunk, uploaded_file, distance = row
        context_chunks.append(
            RetrievedContextChunk(
                file_id=uploaded_file.id,
                file_name=uploaded_file.original_file_name,
                folder_name=uploaded_file.folder_name,
                chunk_index=chunk.chunk_index,
                chunk_text=chunk.chunk_text,
                similarity_score=max(0.0, 1 - float(distance)),
            )
        )
        prompt_context_lines.append(
            f"[{rank}] File: {uploaded_file.original_file_name} (id={uploaded_file.id}, folder={uploaded_file.folder_name}, chunk={chunk.chunk_index})\n"
            f"{chunk.chunk_text}"
        )

    answer = _generate_answer(query=request.query, context_blocks=prompt_context_lines)

    return ChatQueryResponse(
        query=request.query,
        answer=answer,
        context=context_chunks,
    )


def _generate_answer(query: str, context_blocks: list[str]) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is required for chatbot responses")

    client = genai.Client(api_key=api_key)
    model_name = os.getenv("CHAT_MODEL", "gemini-2.0-flash")

    prompt = (
        "You are a retrieval-augmented assistant. Use only the provided context to answer. "
        "If the context is insufficient, say what is missing. Keep answers concise and factual.\n\n"
        f"User query: {query}\n\n"
        "Retrieved context:\n"
        + "\n\n".join(context_blocks)
    )

    response = client.models.generate_content(model=model_name, contents=prompt)
    return response.text or "I found relevant context but could not generate a response."
