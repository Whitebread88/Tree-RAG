import logging

from sqlalchemy import text, update
from sqlmodel import Session, SQLModel

from db import engine
from models import FileProcessingStatus, UploadedFile  # noqa: F401 – ensures table is registered

logger = logging.getLogger(__name__)


def _add_missing_enum_values() -> None:
    """Add any enum values that were introduced after the initial CREATE TYPE."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for member in FileProcessingStatus:
            try:
                conn.execute(
                    text("ALTER TYPE fileprocessingstatus ADD VALUE IF NOT EXISTS :val"),
                    {"val": member.name},
                )
            except Exception as exc:
                logger.warning("Could not add enum value %s: %s", member.name, exc)


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
