from datetime import datetime

from pydantic import BaseModel, Field

from models import FileProcessingStatus


class UploadedFileMetadata(BaseModel):
    id: int
    original_file_name: str
    content_type: str | None
    size_bytes: int
    gcs_path: str
    folder_name: str
    metadata: dict | None
    processing_status: FileProcessingStatus
    created_at: datetime


class FileUploadBatchResponse(BaseModel):
    files: list[UploadedFileMetadata]
    job_triggered: bool
    job_execution_name: str | None = None


class ProcessFilesRequest(BaseModel):
    file_ids: list[int] | None = Field(default=None, description="Specific uploaded file ids to process")
    folder_name: str | None = Field(default=None, description="Process uploaded files in this folder")
    chunk_size: int = 1000
    chunk_overlap: int = 200
    include_completed: bool = Field(default=False, description="When true, also reprocess files that are already completed.")


class ProcessedFileResult(BaseModel):
    file_id: int
    file_name: str
    chunks_created: int
    status: str
    error: str | None = None


class ProcessFilesResponse(BaseModel):
    results: list[ProcessedFileResult]


class ChatQueryRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=20)
    folder_name: str | None = Field(default=None, description="Optional folder filter for retrieval")


class RetrievedContextChunk(BaseModel):
    file_id: int
    file_name: str
    folder_name: str
    chunk_index: int
    chunk_text: str
    similarity_score: float


class ChatQueryResponse(BaseModel):
    query: str
    answer: str
    context: list[RetrievedContextChunk]
