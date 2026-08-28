import logging
import os
import re
from dataclasses import dataclass
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

# The model cites with the [n] rank markers it sees in the context, because
# numbers are far easier for it to reproduce exactly than long file names.
# Those ranks mean nothing to a reader, so they are swapped for the source
# file name after generation.
_CITATION_PATTERN = re.compile(r"\[(\d+)\]")
_SOURCES_LABEL = "Sources: "

_NO_CONTEXT_ANSWER = "I could not find relevant processed chunks for this query."

_SYSTEM_INSTRUCTION = (
    "You are a retrieval-augmented assistant. Answer ONLY using the provided context. "
    "If the context does not contain enough information, say so explicitly and name what is missing. "
    "Cite every claim you make with the [n] marker of the context block it came from, "
    "for example [1] or [2]. Never invent a marker that is not in the context. "
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


@dataclass(frozen=True)
class _HistoryTurn:
    """One prior turn, detached from the ORM.

    History outlives the session that read it, so it cannot be a list of
    ChatMessage instances — those raise DetachedInstanceError once their
    session closes.
    """

    role: str
    content: str


@lru_cache(maxsize=1)
def _get_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is required for chatbot responses")
    return genai.Client(api_key=api_key)


def answer_query(request: ChatQueryRequest) -> ChatQueryResponse:
    """Answer a query against the user's processed chunks.

    Each database phase runs in its own short-lived session so that the three
    Gemini round trips in between hold no pooled connection. The pool is small
    (see db.py), and a single session spanning the whole function would make
    every request occupy a slot for its full duration — turning model latency
    into pool-exhaustion errors on unrelated requests.
    """
    threshold = request.similarity_threshold if request.similarity_threshold is not None else _DEFAULT_SIMILARITY_THRESHOLD

    # Reads the conversation, and rejects one the caller does not own before
    # any billable model call.
    history = _load_history(user_id=request.user_id, conversation_id=request.conversation_id)

    # History has to be loaded before embedding: a follow-up only makes sense
    # as a search query once its references are resolved.
    retrieval_query = _build_retrieval_query(query=request.query, history=history)
    query_embedding = embed_query(retrieval_query)

    context_chunks, prompt_blocks = _retrieve_context(
        user_id=request.user_id,
        query_embedding=query_embedding,
        top_k=request.top_k,
        folder_names=request.folder_names,
        threshold=threshold,
    )

    if not context_chunks:
        answer = _NO_CONTEXT_ANSWER
    else:
        answer = _attach_sources(
            answer=_generate_answer(
                query=request.query,
                context_blocks=prompt_blocks,
                history=history,
            ),
            context_chunks=context_chunks,
        )

    # The conversation is created here rather than up front, so a failed
    # model call cannot leave an empty conversation behind.
    conversation_id = _persist_turn(
        user_id=request.user_id,
        conversation_id=request.conversation_id,
        query=request.query,
        answer=answer,
    )

    return ChatQueryResponse(
        conversation_id=conversation_id,
        query=request.query,
        retrieval_query=retrieval_query,
        answer=answer,
        context=context_chunks,
    )


def _load_history(user_id: str, conversation_id: int | None) -> list[_HistoryTurn]:
    """Phase 1: read prior turns, or nothing at all for a new conversation."""
    if conversation_id is None:
        return []

    with Session(engine) as session:
        conversation = _load_owned_conversation(
            session=session,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        return _load_recent_messages(
            session=session,
            conversation_id=conversation.id,
            limit=_MAX_HISTORY_MESSAGES,
        )


def _retrieve_context(
    user_id: str,
    query_embedding: list[float],
    top_k: int,
    folder_names: list[str] | None,
    threshold: float,
) -> tuple[list[RetrievedContextChunk], list[str]]:
    """Phase 2: nearest-neighbour search, thresholding and dedup."""
    distance = FileChunkEmbedding.embedding.cosine_distance(query_embedding).label("distance")
    statement = (
        select(FileChunkEmbedding, UploadedFile, distance)
        .join(UploadedFile, UploadedFile.id == FileChunkEmbedding.uploaded_file_id)
        .where(UploadedFile.user_id == user_id)
        .order_by(distance.asc())
        .limit(top_k)
    )

    if folder_names:
        statement = statement.where(UploadedFile.folder_name.in_(folder_names))

    with Session(engine) as session:
        rows = session.exec(statement).all()
        # Runs inside the session because rows are ORM instances; everything
        # it returns is plain and safe to use once the session is closed.
        return _build_context(rows=rows, threshold=threshold)


def _persist_turn(user_id: str, conversation_id: int | None, query: str, answer: str) -> int:
    """Phase 3: create the conversation if needed and record both messages."""
    with Session(engine) as session:
        conversation = _resolve_conversation(
            session=session,
            user_id=user_id,
            conversation_id=conversation_id,
            first_user_message=query,
        )
        resolved_id = conversation.id

        session.add(ChatMessage(conversation_id=resolved_id, role="user", content=query))
        session.add(ChatMessage(conversation_id=resolved_id, role="assistant", content=answer))
        conversation.updated_at = datetime.now(timezone.utc)
        session.add(conversation)
        session.commit()

    return resolved_id


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


def _load_recent_messages(session: Session, conversation_id: int, limit: int) -> list[_HistoryTurn]:
    if limit <= 0:
        return []
    rows = session.exec(
        select(ChatMessage.role, ChatMessage.content)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
    ).all()
    return [_HistoryTurn(role=role, content=content) for role, content in reversed(rows)]


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


def _format_history_block(history: list[_HistoryTurn]) -> str:
    if not history:
        return ""
    lines = []
    for msg in history:
        role_label = "User" if msg.role == "user" else "Assistant"
        lines.append(f"{role_label}: {msg.content}")
    return "Conversation so far:\n" + "\n".join(lines) + "\n\n"


def _chat_model() -> str:
    return os.getenv("CHAT_MODEL", "gemini-3.7-flash")


def _build_retrieval_query(query: str, history: list[_HistoryTurn]) -> str:
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


def _attach_sources(answer: str, context_chunks: list[RetrievedContextChunk]) -> str:
    """Turn the model's [n] rank markers into source file names.

    Ranks are an artefact of how the context was assembled and mean nothing
    to whoever reads the answer, so each one is replaced by the file it came
    from and the cited files are listed at the end.
    """
    if not context_chunks:
        return answer

    file_name_by_rank = {index + 1: chunk.file_name for index, chunk in enumerate(context_chunks)}
    cited: list[str] = []

    def remember(file_name: str) -> None:
        if file_name not in cited:
            cited.append(file_name)

    def replace(match: re.Match) -> str:
        file_name = file_name_by_rank.get(int(match.group(1)))
        if file_name is None:
            # A marker the model made up. Leave it be rather than attribute
            # the claim to a document that was never retrieved.
            return match.group(0)
        remember(file_name)
        return f"[{file_name}]"

    rewritten = _CITATION_PATTERN.sub(replace, answer)

    # Earlier turns in the history already carry file-name markers, so the
    # model sometimes copies that style instead of using [n].
    for chunk in context_chunks:
        if f"[{chunk.file_name}]" in rewritten:
            remember(chunk.file_name)

    if not cited:
        return rewritten
    return f"{rewritten}\n\n{_SOURCES_LABEL}{', '.join(cited)}"


def _generate_answer(query: str, context_blocks: list[str], history: list[_HistoryTurn]) -> str:
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
