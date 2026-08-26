import logging
from datetime import datetime, timezone

from google.api_core.exceptions import NotFound
from sqlalchemy import func
from sqlmodel import Session, delete, select

from db import engine
from embeddings_service import embed_chunks
from gcs_service import get_storage_bucket
from models import FileChunkEmbedding, FileProcessingStatus, UploadedFile
from chunking import TextChunk, chunk_segments
from docling_service import create_docling_converter, extract_segments_with_docling
from schemas import ProcessFilesRequest, ProcessFilesResponse, ProcessedFileResult

logger = logging.getLogger(__name__)


async def process_uploaded_files(request: ProcessFilesRequest) -> ProcessFilesResponse:
    with Session(engine) as session:
        _log_processing_status_summary(session=session, stage="before-selection")
        files_to_process = _resolve_files_to_process(session=session, request=request)
        logger.info("Resolved %s files to process", len(files_to_process))
        if not files_to_process:
            logger.warning("No files matched processing criteria (include_completed=%s, file_ids=%s, folder_name=%s)", request.include_completed, request.file_ids, request.folder_name)
        bucket = get_storage_bucket()
        results: list[ProcessedFileResult] = []
        converter: object | None = None

        for uploaded_file in files_to_process:
            try:
                logger.info("Starting file processing for file_id=%s path=%s", uploaded_file.id, uploaded_file.gcs_path)
                resolved_path, file_bytes = _download_file_bytes(bucket=bucket, gcs_path=uploaded_file.gcs_path)
                if resolved_path != uploaded_file.gcs_path:
                    logger.warning(
                        "Recovered from corrupted GCS path for file_id=%s original=%s resolved=%s",
                        uploaded_file.id,
                        uploaded_file.gcs_path,
                        resolved_path,
                    )
                    uploaded_file.gcs_path = resolved_path
                uploaded_file.processing_status = FileProcessingStatus.PROCESSING
                uploaded_file.processing_error = None
                session.add(uploaded_file)
                session.commit()
                session.refresh(uploaded_file)

                session.exec(delete(FileChunkEmbedding).where(FileChunkEmbedding.uploaded_file_id == uploaded_file.id))
                session.flush()

                chunks_created = _try_reuse_existing_embeddings(session=session, uploaded_file=uploaded_file)
                reused_embeddings = chunks_created is not None

                if not reused_embeddings:
                    if converter is None:
                        converter = create_docling_converter()
                    segments = await extract_segments_with_docling(
                        file_name=uploaded_file.original_file_name,
                        file_bytes=file_bytes,
                        converter=converter,
                    )
                    chunks = chunk_segments(
                        segments=segments,
                        chunk_size=request.chunk_size,
                        chunk_overlap=request.chunk_overlap,
                    )
                    headers = [_context_header(uploaded_file=uploaded_file, chunk=c) for c in chunks]
                    vectors = embed_chunks(
                        [_embedding_input(header, c.text) for header, c in zip(headers, chunks)]
                    )

                    for index, (chunk, header, vector) in enumerate(zip(chunks, headers, vectors)):
                        session.add(
                            FileChunkEmbedding(
                                uploaded_file_id=uploaded_file.id,
                                chunk_index=index,
                                chunk_text=chunk.text,
                                context_header=header,
                                embedding=vector,
                                page_number=chunk.page_number,
                                char_offset_start=chunk.char_offset_start,
                                char_offset_end=chunk.char_offset_end,
                            )
                        )
                    chunks_created = len(chunks)

                uploaded_file.processed_at = datetime.now(timezone.utc)
                uploaded_file.processing_error = None
                uploaded_file.processing_status = FileProcessingStatus.COMPLETED
                session.add(uploaded_file)
                session.commit()
                logger.info(
                    "Completed file processing for file_id=%s chunks_created=%s reused=%s",
                    uploaded_file.id,
                    chunks_created,
                    reused_embeddings,
                )

                results.append(
                    ProcessedFileResult(
                        file_id=uploaded_file.id,
                        file_name=uploaded_file.original_file_name,
                        chunks_created=chunks_created,
                        status="reused" if reused_embeddings else "processed",
                    )
                )
            except Exception as exc:  # allow per-file failures without stopping batch
                session.rollback()
                _mark_file_failed(session=session, uploaded_file=uploaded_file, exc=exc)
                logger.exception("File processing failed for file_id=%s: %s", uploaded_file.id, exc)
                results.append(
                    ProcessedFileResult(
                        file_id=uploaded_file.id,
                        file_name=uploaded_file.original_file_name,
                        chunks_created=0,
                        status="failed",
                        error=str(exc),
                    )
                )

        _log_processing_status_summary(session=session, stage="after-processing")
        return ProcessFilesResponse(results=results)


def _try_reuse_existing_embeddings(session: Session, uploaded_file: UploadedFile) -> int | None:
    """If another COMPLETED file shares this content_hash, copy its embeddings.

    Returns the number of chunks copied, or None if no reusable source exists.

    Only embeddings built with a context header are reusable. Older vectors
    were built from bare chunk text, so copying them would silently keep a
    file on the previous retrieval scheme and make reprocessing look like a
    no-op.
    """
    if not uploaded_file.content_hash:
        return None

    source = session.exec(
        select(UploadedFile)
        .where(UploadedFile.content_hash == uploaded_file.content_hash)
        .where(UploadedFile.processing_status == FileProcessingStatus.COMPLETED)
        .where(UploadedFile.id != uploaded_file.id)
        .limit(1)
    ).first()
    if source is None:
        return None

    source_chunks = session.exec(
        select(FileChunkEmbedding)
        .where(FileChunkEmbedding.uploaded_file_id == source.id)
        .order_by(FileChunkEmbedding.chunk_index.asc())
    ).all()
    if not source_chunks:
        return None

    if any(chunk.context_header is None for chunk in source_chunks):
        logger.info(
            "Not reusing embeddings from file_id=%s for file_id=%s: built before context headers",
            source.id,
            uploaded_file.id,
        )
        return None

    logger.info(
        "Reusing %s embeddings from file_id=%s for file_id=%s (content_hash match)",
        len(source_chunks),
        source.id,
        uploaded_file.id,
    )
    for chunk in source_chunks:
        session.add(
            FileChunkEmbedding(
                uploaded_file_id=uploaded_file.id,
                chunk_index=chunk.chunk_index,
                chunk_text=chunk.chunk_text,
                # Copied verbatim: the header has to match what the reused
                # vector was actually built from, not this file's own name.
                context_header=chunk.context_header,
                embedding=chunk.embedding,
                page_number=chunk.page_number,
                char_offset_start=chunk.char_offset_start,
                char_offset_end=chunk.char_offset_end,
            )
        )
    return len(source_chunks)


def _context_header(uploaded_file: UploadedFile, chunk: TextChunk) -> str:
    """Breadcrumb prepended to a chunk before embedding.

    Chunk text on its own carries no signal about which document or section
    it came from, so a chunk reading "the limit is 30 days" will not match a
    query about refund windows. Embedding the breadcrumb with the text puts
    that signal in the vector.
    """
    parts: list[str] = []
    if uploaded_file.folder_name:
        parts.append(uploaded_file.folder_name)
    parts.append(uploaded_file.original_file_name)
    if chunk.heading:
        parts.append(chunk.heading)
    if chunk.page_number is not None:
        parts.append(f"page {chunk.page_number}")
    return " > ".join(parts)


def _embedding_input(context_header: str, chunk_text: str) -> str:
    if not context_header:
        return chunk_text
    return f"{context_header}\n\n{chunk_text}"


def _resolve_files_to_process(session: Session, request: ProcessFilesRequest) -> list[UploadedFile]:
    statement = select(UploadedFile)

    if not request.include_completed:
        statement = statement.where(UploadedFile.processing_status == FileProcessingStatus.UPLOADED)

    if request.file_ids:
        statement = statement.where(UploadedFile.id.in_(request.file_ids))

    if request.folder_name:
        statement = statement.where(UploadedFile.folder_name == request.folder_name)

    return list(session.exec(statement).all())


def _log_processing_status_summary(session: Session, stage: str) -> None:
    status_rows = session.exec(
        select(UploadedFile.processing_status, func.count(UploadedFile.id)).group_by(UploadedFile.processing_status)
    ).all()
    status_counts = {str(status): count for status, count in status_rows}
    logger.info("File status summary (%s): %s", stage, status_counts)


def _download_file_bytes(bucket, gcs_path: str) -> tuple[str, bytes]:
    candidates = _candidate_gcs_paths(gcs_path)
    last_error: NotFound | None = None

    for candidate_path in candidates:
        try:
            blob = bucket.blob(candidate_path)
            return candidate_path, blob.download_as_bytes()
        except NotFound as exc:
            last_error = exc
            logger.warning("GCS object not found for candidate path=%s", candidate_path)

    if last_error is not None:
        raise last_error

    raise RuntimeError(f"Unable to resolve downloadable GCS path for {gcs_path}")


def _candidate_gcs_paths(gcs_path: str) -> list[str]:
    candidates = [gcs_path]
    path_parts = gcs_path.split("/")
    if path_parts:
        folder_name = path_parts[0]
        if folder_name.startswith("string") and len(folder_name) > len("string"):
            corrected_folder = folder_name[len("string") :]
            candidates.append("/".join([corrected_folder, *path_parts[1:]]))
        elif folder_name.startswith("str") and len(folder_name) > len("str"):
            corrected_folder = folder_name[len("str") :]
            candidates.append("/".join([corrected_folder, *path_parts[1:]]))
    return candidates


def _mark_file_failed(session: Session, uploaded_file: UploadedFile, exc: Exception) -> None:
    uploaded_file.processing_status = FileProcessingStatus.FAILED
    uploaded_file.processing_error = str(exc)
    session.add(uploaded_file)
    try:
        session.commit()
    except Exception as status_exc:
        session.rollback()
        logger.exception(
            "Failed to persist FAILED status for file_id=%s due to: %s",
            uploaded_file.id,
            status_exc,
        )
