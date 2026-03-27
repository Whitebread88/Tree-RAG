from fastapi import FastAPI, File, Form, UploadFile
from sqlalchemy import text
from sqlmodel import SQLModel, Session, select

from db import engine
from processing_service import process_uploaded_files
from schemas import FileUploadBatchResponse, ProcessFilesRequest, ProcessFilesResponse
from upload_service import upload_files_and_record_metadata

app = FastAPI()


@app.on_event("startup")
def startup() -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("ALTER TABLE uploaded_files ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ"))
        conn.execute(text("ALTER TABLE uploaded_files ADD COLUMN IF NOT EXISTS processing_error TEXT"))

    SQLModel.metadata.create_all(engine)


@app.get("/health")
def health_check():
    try:
        with Session(engine) as session:
            session.exec(select(1)).one()
        return {"status": "connected", "database": "PostgreSQL is reachable"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/")
def read_root():
    return {"message": "Cloud Run is active"}


@app.post("/files/upload", response_model=FileUploadBatchResponse)
async def upload_files(
    files: list[UploadFile] = File(...),
    folder_name: str = Form(...),
    metadata: str | None = Form(None),
):
    return await upload_files_and_record_metadata(files=files, folder_name=folder_name, metadata=metadata)


@app.post("/files/process", response_model=ProcessFilesResponse)
async def process_files(request: ProcessFilesRequest):
    return await process_uploaded_files(request)
