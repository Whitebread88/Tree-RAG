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
    embedding: list[float] = Field(sa_column=Column(Vector(768), nullable=False))
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), server_default=func.now(), nullable=False),
    )
