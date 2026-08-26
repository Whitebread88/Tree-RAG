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
_MAX_CONTEXT_CHARS = int(os.getenv("RAG_MAX_CONTEXT_CHARS", "100000"))

# Number of most-recent prior messages to feed the LLM as conversation context.
_MAX_HISTORY_MESSAGES = int(os.getenv("CHAT_HISTORY_MESSAGES", "6"))

# Two chunks from the same file whose overlap reaches this fraction of the
# shorter one are treated as the same passage. The ingest sliding window
# overlaps by design, so without this the top-k fills up with near-duplicates
# and the LLM sees far fewer distinct facts than it appears to. Set to 1.0
# to disable.
_DEDUP_OVERLAP_RATIO = float(os.getenv("RAG_DEDUP_OVERLAP_RATIO", "0.5"))

# Rewrite follow-up questions into standalone queries before embedding them.
# Without this, "what about annual plans?" is embedded verbatim and retrieval
# has no idea what the topic is.
_QUERY_REWRITE_ENABLED = os.getenv("RAG_QUERY_REWRITE", "1").strip().lower() not in {"0", "false", "no"}

# A rewrite longer than this is a sign the model explained itself instead of
# answering, so we fall back to the raw query.
_QUERY_REWRITE_MAX_CHARS = 500

_TITLE_MAX_CHARS = 80

_NO_CONTEXT_ANSWER = "I could not find relevant processed chunks for this query."

_SYSTEM_INSTRUCTION = (
    "You are a retrieval-augmented assistant. Answer ONLY using the provided context. "
    "If the context does not contain enough information, say so explicitly and name what is missing. "
    "Cite the sources you used with the [n] markers from the context. "
    "Keep answers concise and factual. "
    "Use the prior conversation only to interpret follow-up questions; never invent facts from it."
)


_QUERY_REWRITE_INSTRUCTION = (
    "Rewrite the user's latest message into a single standalone search query for a document "
    "retrieval system. Resolve pronouns and implicit references using the conversation so far. "
    "Keep the user's own wording, and copy any names, numbers, codes or identifiers exactly. "
    "If the message is already self-contained, return it unchanged. "
    "Return only the query text, with no preamble, quotes or explanation."
)


@lru_cache(maxsize=1)
def _get_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is required for chatbot responses")
    return genai.Client(api_key=api_key)


def answer_query(request: ChatQueryRequest) -> ChatQueryResponse:
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

        # History has to be loaded before embedding: a follow-up only makes
        # sense as a search query once its references are resolved.
        retrieval_query = _build_retrieval_query(query=request.query, history=history)
        query_embedding = embed_query(retrieval_query)

        distance = FileChunkEmbedding.embedding.cosine_distance(query_embedding).label("distance")
        statement = (
            select(FileChunkEmbedding, UploadedFile, distance)
            .join(UploadedFile, UploadedFile.id == FileChunkEmbedding.uploaded_file_id)
            .where(UploadedFile.user_id == request.user_id)
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
        retrieval_query=retrieval_query,
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
    kept_chunks: list[FileChunkEmbedding] = []
    used_chars = 0

    for row in rows:
        chunk, uploaded_file, distance = row
        similarity = max(0.0, 1 - float(distance))
        if similarity < threshold:
            continue
        # Rows arrive best-first, so the duplicate we drop is always the
        # lower-scoring window over text we already have.
        if _is_duplicate(chunk, kept_chunks):
            continue

        block = _format_prompt_block(
            rank=len(prompt_blocks) + 1,
            file_name=uploaded_file.original_file_name,
            page_number=chunk.page_number,
            chunk_index=chunk.chunk_index,
            chunk_text=chunk.chunk_text,
            context_header=chunk.context_header,
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
                context_header=chunk.context_header,
                similarity_score=similarity,
                page_number=chunk.page_number,
            )
        )
        prompt_blocks.append(block)
        kept_chunks.append(chunk)
        used_chars += len(block)

    return context_chunks, prompt_blocks


def _is_duplicate(candidate: FileChunkEmbedding, kept: list[FileChunkEmbedding]) -> bool:
    if _DEDUP_OVERLAP_RATIO >= 1.0:
        return False
    for existing in kept:
        if existing.uploaded_file_id != candidate.uploaded_file_id:
            continue
        if _overlap_ratio(candidate, existing) >= _DEDUP_OVERLAP_RATIO:
            return True
    return False


def _overlap_ratio(a: FileChunkEmbedding, b: FileChunkEmbedding) -> float:
    span_ratio = _char_span_overlap(a, b)
    if span_ratio is not None:
        return span_ratio
    return _token_overlap(a.chunk_text, b.chunk_text)


def _char_span_overlap(a: FileChunkEmbedding, b: FileChunkEmbedding) -> float | None:
    """Overlap as a fraction of the shorter chunk, or None if offsets are missing.

    Offsets are exact for anything ingested after they were added, which makes
    this the cheap and reliable path for sliding-window duplicates.
    """
    offsets = (a.char_offset_start, a.char_offset_end, b.char_offset_start, b.char_offset_end)
    if any(offset is None for offset in offsets):
        return None

    overlap = min(a.char_offset_end, b.char_offset_end) - max(a.char_offset_start, b.char_offset_start)
    if overlap <= 0:
        return 0.0

    shortest = min(a.char_offset_end - a.char_offset_start, b.char_offset_end - b.char_offset_start)
    if shortest <= 0:
        return 0.0
    return overlap / shortest


def _token_overlap(a: str, b: str) -> float:
    """Fallback for legacy rows with no char offsets."""
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / min(len(tokens_a), len(tokens_b))


def _format_prompt_block(
    rank: int,
    file_name: str,
    page_number: int | None,
    chunk_index: int,
    chunk_text: str,
    context_header: str | None = None,
) -> str:
    if context_header:
        location = context_header
    else:
        # Legacy rows ingested before headers existed.
        where = f"page {page_number}" if page_number is not None else f"chunk {chunk_index}"
        location = f"{file_name} ({where})"
    return f"[{rank}] {location}\n{chunk_text}"


def _format_history_block(history: list[ChatMessage]) -> str:
    if not history:
        return ""
    lines = []
    for msg in history:
        role_label = "User" if msg.role == "user" else "Assistant"
        lines.append(f"{role_label}: {msg.content}")
    return "Conversation so far:\n" + "\n".join(lines) + "\n\n"


def _chat_model() -> str:
    return os.getenv("CHAT_MODEL", "gemini-3.7-flash")


def _build_retrieval_query(query: str, history: list[ChatMessage]) -> str:
    """Resolve a follow-up into a standalone query before it is embedded.

    Falls back to the raw query on any failure — a degraded search beats a
    failed request.
    """
    if not _QUERY_REWRITE_ENABLED or not history:
        return query

    prompt = (
        f"{_QUERY_REWRITE_INSTRUCTION}\n\n"
        f"{_format_history_block(history)}"
        f"Latest message: {query}\n\n"
        "Standalone search query:"
    )

    try:
        client = _get_client()
        response = client.models.generate_content(
            model=os.getenv("QUERY_REWRITE_MODEL", _chat_model()),
            contents=prompt,
        )
        rewritten = _clean_rewritten_query(response.text or "")
    except Exception as exc:
        logger.warning("Query rewrite failed; falling back to the raw query: %s", exc)
        return query

    if not rewritten or len(rewritten) > _QUERY_REWRITE_MAX_CHARS:
        logger.info("Discarding unusable query rewrite (length=%s)", len(rewritten))
        return query

    if rewritten != query:
        logger.info("Rewrote retrieval query: %r -> %r", query, rewritten)
    return rewritten


def _clean_rewritten_query(text: str) -> str:
    cleaned = text.strip()
    # Models often wrap the answer in quotes despite being told not to.
    for quote in ('"', "'"):
        if len(cleaned) > 1 and cleaned.startswith(quote) and cleaned.endswith(quote):
            cleaned = cleaned[1:-1].strip()
            break
    return cleaned


def _generate_answer(query: str, context_blocks: list[str], history: list[ChatMessage]) -> str:
    client = _get_client()
    model_name = _chat_model()

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
