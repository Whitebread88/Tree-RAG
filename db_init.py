import logging

from sqlalchemy import text, update
from sqlmodel import Session, SQLModel

from db import engine
from models import FileProcessingStatus, UploadedFile  # noqa: F401 – ensures table is registered

logger = logging.getLogger(__name__)


def ensure_database_schema() -> None:
    with Session(engine) as session:
        try:
            session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.warning("Could not create pgvector extension (may already exist or require superuser): %s", exc)

    SQLModel.metadata.create_all(engine)

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
