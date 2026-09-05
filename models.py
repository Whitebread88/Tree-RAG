from datetime import datetime
from enum import Enum

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, Enum as SAEnum, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class FileProcessingStatus(str, Enum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class UploadedFile(SQLModel, table=True):
    __tablename__ = "uploaded_files"

    id: int | None = Field(default=None, primary_key=True)
    original_file_name: str
    content_type: str | None = None
    size_bytes: int
    gcs_path: str
    folder_name: str
    user_id: str = Field(
        sa_column=Column(ForeignKey("users.id"), nullable=False, index=True)
    )
    file_metadata: dict | None = Field(default=None, sa_column=Column("metadata", JSONB, nullable=True))
    processing_status: FileProcessingStatus = Field(
        default=FileProcessingStatus.UPLOADED,
        sa_column=Column(
            SAEnum(
                FileProcessingStatus,
                name="fileprocessingstatus",
            ),
            nullable=False,
        ),
    )
    processed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    processing_error: str | None = None
    # Set when a file completed but only part of it could be parsed, so a
    # truncated extraction is not indistinguishable from a clean one.
    processing_warning: str | None = None
    content_hash: str | None = Field(default=None, index=True)
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )


class FileChunkEmbedding(SQLModel, table=True):
    __tablename__ = "file_chunk_embeddings"

    id: int | None = Field(default=None, primary_key=True)
    uploaded_file_id: int = Field(sa_column=Column(ForeignKey("uploaded_files.id"), nullable=False))
    chunk_index: int
    chunk_text: str = Field(sa_column=Column(Text, nullable=False))
    # The breadcrumb (folder > file > heading > page) that was prepended to
    # chunk_text before embedding. Kept so citations and debugging can show
    # exactly what the vector was built from.
    context_header: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    embedding: list[float] = Field(sa_column=Column(Vector(768), nullable=False))
    page_number: int | None = None
    char_offset_start: int | None = None
    char_offset_end: int | None = None
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: str = Field(primary_key=True)
    name: str
    last_login_date: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, nullable=False)
    title: str | None = None
    folder_names: list[str] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_messages"

    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(
        sa_column=Column(
            ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    role: str = Field(nullable=False)
    content: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )
