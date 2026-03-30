from sqlmodel import Session, SQLModel, select

from db import engine
from models import FileProcessingStatus, UploadedFile


def ensure_database_schema() -> None:
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        files = list(session.exec(select(UploadedFile)).all())
        for uploaded_file in files:
            if uploaded_file.processing_status is None:
                uploaded_file.processing_status = FileProcessingStatus.UPLOADED
                session.add(uploaded_file)
        session.commit()
