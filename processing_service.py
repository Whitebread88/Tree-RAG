import logging
from datetime import datetime, timezone

from sqlalchemy import func
from sqlmodel import Session, delete, select

from db import engine
from embeddings_service import embed_chunks
from gcs_service import get_storage_bucket
from models import FileChunkEmbedding, FileProcessingStatus, UploadedFile
from chunking import chunk_text
from docling_service import extract_text_with_docling
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

        for uploaded_file in files_to_process:
            try:
                logger.info("Starting file processing for file_id=%s path=%s", uploaded_file.id, uploaded_file.gcs_path)
                blob = bucket.blob(uploaded_file.gcs_path)
                file_bytes = blob.download_as_bytes()
                uploaded_file.processing_status = FileProcessingStatus.PROCESSING
                uploaded_file.processing_error = None
                session.add(uploaded_file)
                session.commit()
                session.refresh(uploaded_file)

                text = await extract_text_with_docling(
                    file_name=uploaded_file.original_file_name,
                    file_bytes=file_bytes,
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
                uploaded_file.processing_status = FileProcessingStatus.FAILED
                uploaded_file.processing_error = str(exc)
                session.add(uploaded_file)
                session.commit()
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
