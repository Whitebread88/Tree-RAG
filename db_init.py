from sqlalchemy import text
from sqlmodel import Session, SQLModel

from db import engine
from models import FileProcessingStatus, UploadedFile  # noqa: F401 – ensures table is registered


def ensure_database_schema() -> None:
    with Session(engine) as session:
        session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        session.commit()

    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        # Migrate legacy records that have NULL processing_status
        session.execute(
            text("UPDATE uploaded_files SET processing_status = :status WHERE processing_status IS NULL"),
            {"status": FileProcessingStatus.UPLOADED.value},
        )
        # Recover files stuck in PROCESSING (e.g. from a crashed job)
        session.execute(
            text("UPDATE uploaded_files SET processing_status = :target WHERE processing_status = :stuck"),
            {"target": FileProcessingStatus.UPLOADED.value, "stuck": FileProcessingStatus.PROCESSING.value},
        )
        session.commit()
