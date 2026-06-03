from datetime import datetime

from sqlmodel import Field, SQLModel

from models import FileProcessingStatus


class UploadedFileMetadata(SQLModel):
    id: int
    original_file_name: str
    content_type: str | None
    size_bytes: int
    gcs_path: str
    folder_name: str
    file_metadata: dict | None = Field(default=None, alias="metadata", serialization_alias="metadata")
    processing_status: FileProcessingStatus
    created_at: datetime


class FileUploadBatchResponse(SQLModel):
    files: list[UploadedFileMetadata]
    job_triggered: bool
    job_execution_name: str | None = None


class ProcessFilesRequest(SQLModel):
    file_ids: list[int] | None = Field(default=None, description="Specific uploaded file ids to process")
    folder_name: str | None = Field(default=None, description="Process uploaded files in this folder")
    chunk_size: int = 1000
    chunk_overlap: int = 200
    include_completed: bool = Field(default=False, description="When true, also reprocess files that are already completed.")


class ProcessedFileResult(SQLModel):
    file_id: int
    file_name: str
    chunks_created: int
    status: str
    error: str | None = None


class ProcessFilesResponse(SQLModel):
    results: list[ProcessedFileResult]


class ChatQueryRequest(SQLModel):
    user_id: str = Field(description="Identifier for the end user owning this conversation.")
    conversation_id: int | None = Field(
        default=None,
        description="Existing conversation to continue. If omitted, a new conversation is created.",
    )
    query: str
    top_k: int = Field(default=5, ge=1, le=20)
    folder_names: list[str] | None = Field(
        default=None,
        max_length=50,
        description="Optional list of folder names to restrict retrieval to.",
    )
    similarity_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Drop chunks with cosine similarity below this threshold (0..1).",
    )


class RetrievedContextChunk(SQLModel):
    file_id: int
    file_name: str
    folder_name: str
    chunk_index: int
    chunk_text: str
    similarity_score: float
    page_number: int | None = None


class ChatQueryResponse(SQLModel):
    conversation_id: int
    query: str
    answer: str
    context: list[RetrievedContextChunk]


class ConversationSummary(SQLModel):
    id: int
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(SQLModel):
    conversations: list[ConversationSummary]


class ChatMessageItem(SQLModel):
    id: int
    role: str
    content: str
    created_at: datetime


class ConversationMessagesResponse(SQLModel):
    conversation_id: int
    user_id: str
    title: str | None
    messages: list[ChatMessageItem]


class UserLoginRequest(SQLModel):
    id: str = Field(description="The user's unique email address.")
    name: str


class UserResponse(SQLModel):
    id: str
    name: str
    last_login_date: datetime
