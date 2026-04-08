import logging

from sqlalchemy import text
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
            text("UPDATE uploaded_files SET processing_status = CAST(:status AS fileprocessingstatus) WHERE processing_status IS NULL"),
            {"status": FileProcessingStatus.UPLOADED.value},
        )
        # Recover files stuck in PROCESSING (e.g. from a crashed job)
        session.execute(
            text("UPDATE uploaded_files SET processing_status = CAST(:target AS fileprocessingstatus) WHERE processing_status = CAST(:stuck AS fileprocessingstatus)"),
            {"target": FileProcessingStatus.UPLOADED.value, "stuck": FileProcessingStatus.PROCESSING.value},
        )
        session.commit()
