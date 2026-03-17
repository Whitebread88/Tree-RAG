from fastapi import FastAPI, File, Form, UploadFile
from sqlalchemy import text

from db import engine
from ingestion_service import ingest_files_and_build_hierarchy
from schemas import IngestResponse

app = FastAPI()


@app.get("/health")
def health_check():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "connected", "database": "PostgreSQL is reachable"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/")
def read_root():
    return {"message": "Cloud Run is active"}


@app.post("/ingest-hierarchy", response_model=IngestResponse)
async def ingest_hierarchy(
    files: list[UploadFile] = File(...),
    folder_name: str = Form(...),
    metadata: str | None = Form(None),
):
    return await ingest_files_and_build_hierarchy(files=files, folder_name=folder_name, metadata=metadata)
