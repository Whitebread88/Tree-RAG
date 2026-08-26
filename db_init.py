import logging

from sqlalchemy import text, update
from sqlmodel import Session, SQLModel

from db import engine
from models import FileProcessingStatus, UploadedFile  # noqa: F401 – ensures table is registered

logger = logging.getLogger(__name__)


def _add_missing_enum_values() -> None:
    """Add any enum values that were introduced after the initial CREATE TYPE.

    Note: We use an explicit commit() after each ALTER TYPE instead of
    AUTOCOMMIT isolation_level, because the Cloud SQL Connector + pg8000
    driver ignores SQLAlchemy's isolation_level setting.  PostgreSQL 12+
    allows ALTER TYPE ADD VALUE inside a transaction block, so an explicit
    commit is safe and reliable.
    """
    with engine.connect() as conn:
        for member in FileProcessingStatus:
            try:
                conn.execute(text(
                    f"ALTER TYPE fileprocessingstatus ADD VALUE IF NOT EXISTS '{member.name}'"
                ))
                conn.commit()
            except Exception as exc:
                conn.rollback()
                logger.warning("Could not add enum value %s: %s", member.name, exc)


def _ensure_vector_indexes() -> None:
    """Create an HNSW index on the embedding column for fast cosine similarity search."""
    with engine.connect() as conn:
        try:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS file_chunk_embeddings_embedding_hnsw_idx "
                "ON file_chunk_embeddings USING hnsw (embedding vector_cosine_ops)"
            ))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            logger.warning("Could not create HNSW index on file_chunk_embeddings.embedding: %s", exc)


# ALTER TABLE statements that bring legacy schemas in line with the current
# SQLModel definitions. SQLModel.metadata.create_all only creates missing
# tables, never new columns or indexes on existing ones, so column additions
# need to be applied explicitly here.
_COLUMN_MIGRATIONS: tuple[str, ...] = (
    "ALTER TABLE uploaded_files ADD COLUMN IF NOT EXISTS content_hash TEXT",
    "ALTER TABLE uploaded_files ADD COLUMN IF NOT EXISTS user_id TEXT REFERENCES users(id)",
    "ALTER TABLE file_chunk_embeddings ADD COLUMN IF NOT EXISTS context_header TEXT",
    "ALTER TABLE file_chunk_embeddings ADD COLUMN IF NOT EXISTS page_number INTEGER",
    "ALTER TABLE file_chunk_embeddings ADD COLUMN IF NOT EXISTS char_offset_start INTEGER",
    "ALTER TABLE file_chunk_embeddings ADD COLUMN IF NOT EXISTS char_offset_end INTEGER",
)

_SUPPORT_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS uploaded_files_content_hash_idx ON uploaded_files (content_hash)",
    "CREATE INDEX IF NOT EXISTS uploaded_files_user_id_idx ON uploaded_files (user_id)",
)


def _apply_column_migrations() -> None:
    with engine.connect() as conn:
        for statement in _COLUMN_MIGRATIONS + _SUPPORT_INDEXES:
            try:
                conn.execute(text(statement))
                conn.commit()
            except Exception as exc:
                conn.rollback()
                logger.warning("Schema migration failed (%s): %s", statement, exc)


def ensure_database_schema() -> None:
    with Session(engine) as session:
        try:
            session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.warning("Could not create pgvector extension (may already exist or require superuser): %s", exc)

    SQLModel.metadata.create_all(engine)
    _add_missing_enum_values()
    _apply_column_migrations()
    _ensure_vector_indexes()

    with Session(engine) as session:
        # Migrate legacy records that have NULL processing_status
        session.execute(
            update(UploadedFile)
            .where(UploadedFile.processing_status.is_(None))
            .values(processing_status=FileProcessingStatus.UPLOADED)
        )
        # Recover files stuck in PROCESSING (e.g. from a crashed job)
        session.execute(
            update(UploadedFile)
            .where(UploadedFile.processing_status == FileProcessingStatus.PROCESSING)
            .values(processing_status=FileProcessingStatus.UPLOADED)
        )
        session.commit()
