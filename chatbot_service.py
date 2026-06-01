import logging
import os
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import HTTPException
from google import genai
from sqlmodel import Session, select

from db import engine
from embeddings_service import embed_query
from models import ChatMessage, Conversation, FileChunkEmbedding, UploadedFile
from schemas import (
    ChatMessageItem,
    ChatQueryRequest,
    ChatQueryResponse,
    ConversationListResponse,
    ConversationMessagesResponse,
    ConversationSummary,
    RetrievedContextChunk,
)

logger = logging.getLogger(__name__)

# Default similarity floor; chunks below this are dropped from the LLM prompt
# so they can't poison answers with irrelevant context. Tune empirically.
_DEFAULT_SIMILARITY_THRESHOLD = float(os.getenv("RAG_SIMILARITY_THRESHOLD", "0.3"))

# Soft cap on the total characters of retrieved context handed to the LLM.
# Gemini Flash has a huge context window, but more context here means more
# noise dilution, not better answers.
_MAX_CONTEXT_CHARS = int(os.getenv("RAG_MAX_CONTEXT_CHARS", "12000"))

# Number of most-recent prior messages to feed the LLM as conversation context.
_MAX_HISTORY_MESSAGES = int(os.getenv("CHAT_HISTORY_MESSAGES", "6"))

_TITLE_MAX_CHARS = 80

_NO_CONTEXT_ANSWER = "I could not find relevant processed chunks for this query."

_SYSTEM_INSTRUCTION = (
    "You are a retrieval-augmented assistant. Answer ONLY using the provided context. "
    "If the context does not contain enough information, say so explicitly and name what is missing. "
    "Cite the sources you used with the [n] markers from the context. "
    "Keep answers concise and factual. "
    "Use the prior conversation only to interpret follow-up questions; never invent facts from it."
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
        conversation = _resolve_conversation(
            session=session,
            user_id=request.user_id,
            conversation_id=request.conversation_id,
            first_user_message=request.query,
        )

        history = _load_recent_messages(
            session=session,
            conversation_id=conversation.id,
            limit=_MAX_HISTORY_MESSAGES,
        )

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
            answer = _NO_CONTEXT_ANSWER
        else:
            answer = _generate_answer(
                query=request.query,
                context_blocks=prompt_blocks,
                history=history,
            )

        session.add(ChatMessage(conversation_id=conversation.id, role="user", content=request.query))
        session.add(ChatMessage(conversation_id=conversation.id, role="assistant", content=answer))
        conversation.updated_at = datetime.now(timezone.utc)
        session.add(conversation)
        session.commit()
        session.refresh(conversation)

        conversation_id = conversation.id

    return ChatQueryResponse(
        conversation_id=conversation_id,
        query=request.query,
        answer=answer,
        context=context_chunks,
    )


def list_conversations(user_id: str) -> ConversationListResponse:
    with Session(engine) as session:
        rows = session.exec(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
        ).all()
        summaries = [
            ConversationSummary(
                id=c.id,
                title=c.title,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in rows
        ]
    return ConversationListResponse(conversations=summaries)


def get_conversation_messages(user_id: str, conversation_id: int) -> ConversationMessagesResponse:
    with Session(engine) as session:
        conversation = _load_owned_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
        messages = session.exec(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.id.asc())
        ).all()
        items = [
            ChatMessageItem(id=m.id, role=m.role, content=m.content, created_at=m.created_at)
            for m in messages
        ]
        return ConversationMessagesResponse(
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            title=conversation.title,
            messages=items,
        )


def delete_conversation(user_id: str, conversation_id: int) -> None:
    with Session(engine) as session:
        conversation = _load_owned_conversation(session=session, user_id=user_id, conversation_id=conversation_id)
        # ON DELETE CASCADE on chat_messages.conversation_id handles message rows.
        session.delete(conversation)
        session.commit()


def _resolve_conversation(
    session: Session,
    user_id: str,
    conversation_id: int | None,
    first_user_message: str,
) -> Conversation:
    if conversation_id is not None:
        return _load_owned_conversation(session=session, user_id=user_id, conversation_id=conversation_id)

    conversation = Conversation(user_id=user_id, title=_derive_title(first_user_message))
    session.add(conversation)
    session.flush()
    session.refresh(conversation)
    return conversation


def _load_owned_conversation(session: Session, user_id: str, conversation_id: int) -> Conversation:
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail=f"Conversation {conversation_id} not found")
    if conversation.user_id != user_id:
        raise HTTPException(status_code=403, detail="Conversation does not belong to this user")
    return conversation


def _load_recent_messages(session: Session, conversation_id: int, limit: int) -> list[ChatMessage]:
    if limit <= 0:
        return []
    rows = session.exec(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))


def _derive_title(text: str) -> str | None:
    cleaned = " ".join(text.split())
    if not cleaned:
        return None
    if len(cleaned) <= _TITLE_MAX_CHARS:
        return cleaned
    return cleaned[: _TITLE_MAX_CHARS - 1].rstrip() + "…"


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


def _format_history_block(history: list[ChatMessage]) -> str:
    if not history:
        return ""
    lines = []
    for msg in history:
        role_label = "User" if msg.role == "user" else "Assistant"
        lines.append(f"{role_label}: {msg.content}")
    return "Conversation so far:\n" + "\n".join(lines) + "\n\n"


def _generate_answer(query: str, context_blocks: list[str], history: list[ChatMessage]) -> str:
    client = _get_client()
    model_name = os.getenv("CHAT_MODEL", "gemini-2.0-flash")

    prompt = (
        f"{_SYSTEM_INSTRUCTION}\n\n"
        f"{_format_history_block(history)}"
        f"Question: {query}\n\n"
        "Context:\n"
        + "\n\n".join(context_blocks)
        + "\n\nAnswer:"
    )

    response = client.models.generate_content(model=model_name, contents=prompt)
    return response.text or "I found relevant context but could not generate a response."
