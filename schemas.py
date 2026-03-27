from datetime import datetime

from pydantic import BaseModel, Field


class UploadedFileMetadata(BaseModel):
    id: int
    original_file_name: str
    content_type: str | None
    size_bytes: int
    gcs_path: str
    folder_name: str
    metadata: dict | None
    created_at: datetime


class FileUploadBatchResponse(BaseModel):
    files: list[UploadedFileMetadata]


class ProcessFilesRequest(BaseModel):
    file_ids: list[int] | None = Field(default=None, description="Specific uploaded file ids to process")
    folder_name: str | None = Field(default=None, description="Process uploaded files in this folder")
    chunk_size: int = 1000
    chunk_overlap: int = 200


class ProcessedFileResult(BaseModel):
    file_id: int
    file_name: str
    chunks_created: int
    status: str
    error: str | None = None


class ProcessFilesResponse(BaseModel):
    results: list[ProcessedFileResult]
