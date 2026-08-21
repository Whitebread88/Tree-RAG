import hashlib
import json
import os
import uuid

from fastapi import HTTPException, UploadFile
from sqlmodel import Session, select

from cloud_run_jobs_service import trigger_file_processing_job
from db import engine
from gcs_service import get_storage_bucket
from models import FileProcessingStatus, UploadedFile, User
from schemas import FolderProcessingStatusResponse, FileUploadBatchResponse, UploadedFileMetadata


async def upload_files_and_record_metadata(
    files: list[UploadFile],
    folder_name: str,
    user_id: str,
    metadata: str | None = None,
) -> FileUploadBatchResponse:
    bucket = get_storage_bucket()
    parsed_metadata = _parse_metadata(metadata)
    uploaded_files: list[UploadedFileMetadata] = []

    with Session(engine) as session:
        if session.get(User, user_id) is None:
            raise HTTPException(status_code=404, detail="User not found. Call /users/login before uploading files.")

        for file in files:
            file_bytes = await file.read()
            size_bytes = len(file_bytes)

            if size_bytes == 0:
                raise HTTPException(status_code=400, detail=f"File is empty: {file.filename}")

            content_hash = hashlib.sha256(file_bytes).hexdigest()

            destination_path = _build_gcs_path(folder_name=folder_name, filename=file.filename)
            blob = bucket.blob(destination_path)
            blob.upload_from_string(file_bytes, content_type=file.content_type)

            uploaded_file = UploadedFile(
                original_file_name=file.filename,
                content_type=file.content_type,
                size_bytes=size_bytes,
                gcs_path=destination_path,
                folder_name=folder_name,
                user_id=user_id,
                file_metadata=parsed_metadata,
                processing_status=FileProcessingStatus.UPLOADED,
                content_hash=content_hash,
            )
            session.add(uploaded_file)
            session.flush()
            session.refresh(uploaded_file)

            uploaded_files.append(
                UploadedFileMetadata(
                    id=uploaded_file.id,
                    original_file_name=uploaded_file.original_file_name,
                    content_type=uploaded_file.content_type,
                    size_bytes=uploaded_file.size_bytes,
                    gcs_path=uploaded_file.gcs_path,
                    folder_name=uploaded_file.folder_name,
                    user_id=uploaded_file.user_id,
                    file_metadata=uploaded_file.file_metadata,
                    processing_status=uploaded_file.processing_status,
                    created_at=uploaded_file.created_at,
                )
            )
        session.commit()

    try:
        job_execution_name = trigger_file_processing_job()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return FileUploadBatchResponse(files=uploaded_files, job_triggered=True, job_execution_name=job_execution_name)


def _build_gcs_path(folder_name: str, filename: str) -> str:
    normalized_folder = folder_name.strip("/")
    sanitized_name = os.path.basename(filename)
    unique_suffix = uuid.uuid4().hex
    return f"{normalized_folder}/{unique_suffix}_{sanitized_name}"


def get_folder_processing_status(user_id: str, folder_name: str) -> FolderProcessingStatusResponse:
    """Return the aggregate processing status for one user's folder."""
    with Session(engine) as session:
        statuses = session.exec(
            select(UploadedFile.processing_status).where(
                UploadedFile.user_id == user_id,
                UploadedFile.folder_name == folder_name,
            )
        ).all()

    if not statuses:
        raise HTTPException(status_code=404, detail="No files found for this user and folder")

    if FileProcessingStatus.FAILED in statuses:
        status = "FAILED"
    elif any(
        file_status in {FileProcessingStatus.UPLOADED, FileProcessingStatus.PROCESSING}
        for file_status in statuses
    ):
        status = "PROCESSING"
    else:
        status = "COMPLETED"

    return FolderProcessingStatusResponse(
        folder_name=folder_name,
        user_id=user_id,
        processing_status=status,
    )


def _parse_metadata(metadata: str | None) -> dict | None:
    if metadata is None:
        return None

    try:
        parsed = json.loads(metadata)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="metadata must be valid JSON") from exc

    if parsed is not None and not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="metadata must be a JSON object")

    return parsed
