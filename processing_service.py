import logging
from datetime import datetime, timezone

from google.api_core.exceptions import NotFound
from sqlalchemy import func
from sqlmodel import Session, delete, select

from db import engine
from embeddings_service import embed_chunks
from gcs_service import get_storage_bucket
from models import FileChunkEmbedding, FileProcessingStatus, UploadedFile
from chunking import chunk_text
from docling_service import create_docling_converter, extract_text_with_docling
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
        converter = create_docling_converter() if files_to_process else None

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

                text = await extract_text_with_docling(
                    file_name=uploaded_file.original_file_name,
                    file_bytes=file_bytes,
                    converter=converter,
                )
                chunks = chunk_text(text=text, chunk_size=request.chunk_size, chunk_overlap=request.chunk_overlap)
                vectors = embed_chunks(chunks)

                session.exec(delete(FileChunkEmbedding).where(FileChunkEmbedding.uploaded_file_id == uploaded_file.id))
                session.flush()

                for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
                    session.add(
                        FileChunkEmbedding(
                            uploaded_file_id=uploaded_file.id,
                            chunk_index=index,
                            chunk_text=chunk,
                            embedding=vector,
                        )
                    )

                uploaded_file.processed_at = datetime.now(timezone.utc)
                uploaded_file.processing_error = None
                uploaded_file.processing_status = FileProcessingStatus.COMPLETED
                session.add(uploaded_file)
                session.commit()
                logger.info(
                    "Completed file processing for file_id=%s chunks_created=%s",
                    uploaded_file.id,
                    len(chunks),
                )

                results.append(
                    ProcessedFileResult(
                        file_id=uploaded_file.id,
                        file_name=uploaded_file.original_file_name,
                        chunks_created=len(chunks),
                        status="processed",
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
