from datetime import datetime, timezone

from sqlmodel import Session, delete, select

from db import engine
from embeddings_service import embed_chunks
from gcs_service import get_storage_bucket
from models import FileChunkEmbedding, FileProcessingStatus, UploadedFile
from chunking import chunk_text
from raganything_service import extract_text_with_raganything
from schemas import ProcessFilesRequest, ProcessFilesResponse, ProcessedFileResult


async def process_uploaded_files(request: ProcessFilesRequest) -> ProcessFilesResponse:
    with Session(engine) as session:
        files_to_process = _resolve_files_to_process(session=session, request=request)
        bucket = get_storage_bucket()
        results: list[ProcessedFileResult] = []

        for uploaded_file in files_to_process:
            try:
                blob = bucket.blob(uploaded_file.gcs_path)
                file_bytes = blob.download_as_bytes()
                uploaded_file.processing_status = FileProcessingStatus.PROCESSING
                uploaded_file.processing_error = None
                session.add(uploaded_file)
                session.commit()
                session.refresh(uploaded_file)

                text = await extract_text_with_raganything(
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
                uploaded_file.processing_status = FileProcessingStatus.UPLOADED
                uploaded_file.processing_error = str(exc)
                session.add(uploaded_file)
                session.commit()
                results.append(
                    ProcessedFileResult(
                        file_id=uploaded_file.id,
                        file_name=uploaded_file.original_file_name,
                        chunks_created=0,
                        status="failed",
                        error=str(exc),
                    )
                )

    return ProcessFilesResponse(results=results)


def _resolve_files_to_process(session: Session, request: ProcessFilesRequest) -> list[UploadedFile]:
    statement = select(UploadedFile)

    if request.file_ids:
        statement = statement.where(UploadedFile.id.in_(request.file_ids))

    if request.folder_name:
        statement = statement.where(UploadedFile.folder_name == request.folder_name)

    files = list(session.exec(statement).all())
    if request.include_completed:
        return files

    return [
        uploaded_file
        for uploaded_file in files
        if uploaded_file.processing_status in (None, FileProcessingStatus.UPLOADED)
    ]
