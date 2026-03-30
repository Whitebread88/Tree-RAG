import json
import os
import uuid

from fastapi import HTTPException, UploadFile
from sqlmodel import Session

from cloud_run_jobs_service import trigger_file_processing_job
from db import engine
from gcs_service import get_storage_bucket
from models import FileProcessingStatus, UploadedFile
from schemas import FileUploadBatchResponse, UploadedFileMetadata


async def upload_files_and_record_metadata(
    files: list[UploadFile],
    folder_name: str,
    metadata: str | None = None,
) -> FileUploadBatchResponse:
    bucket = get_storage_bucket()
    parsed_metadata = _parse_metadata(metadata)
    uploaded_files: list[UploadedFileMetadata] = []

    with Session(engine) as session:
        for file in files:
            file_bytes = await file.read()
            if not file_bytes:
                raise HTTPException(status_code=400, detail=f"File is empty: {file.filename}")

            destination_path = _build_gcs_path(folder_name=folder_name, filename=file.filename)
            blob = bucket.blob(destination_path)
            blob.upload_from_string(file_bytes, content_type=file.content_type)

            uploaded_file = UploadedFile(
                original_file_name=file.filename,
                content_type=file.content_type,
                size_bytes=len(file_bytes),
                gcs_path=destination_path,
                folder_name=folder_name,
                file_metadata=parsed_metadata,
                processing_status=FileProcessingStatus.UPLOADED,
            )
            session.add(uploaded_file)
            session.commit()
            session.refresh(uploaded_file)

            uploaded_files.append(
                UploadedFileMetadata(
                    id=uploaded_file.id,
                    original_file_name=uploaded_file.original_file_name,
                    content_type=uploaded_file.content_type,
                    size_bytes=uploaded_file.size_bytes,
                    gcs_path=uploaded_file.gcs_path,
                    folder_name=uploaded_file.folder_name,
                    metadata=uploaded_file.file_metadata,
                    processing_status=uploaded_file.processing_status,
                    created_at=uploaded_file.created_at,
                )
            )

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
