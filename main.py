from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import JSONResponse
from sqlmodel import Session, select
from chatbot_service import answer_query
from db import close_connector, engine
from db_init import ensure_database_schema
from schemas import ChatQueryRequest, ChatQueryResponse, FileUploadBatchResponse
from upload_service import upload_files_and_record_metadata

app = FastAPI()


@app.on_event("startup")
def startup() -> None:
    ensure_database_schema()


@app.on_event("shutdown")
def shutdown() -> None:
    close_connector()


@app.get("/health")
def health_check():
    try:
        with Session(engine) as session:
            session.exec(select(1)).one()
        return {"status": "connected", "database": "PostgreSQL is reachable"}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "error", "message": str(e)})


@app.get("/")
def read_root():
    return {"message": "Cloud Run is active"}


@app.post("/files/upload", response_model=FileUploadBatchResponse)
async def upload_files(
    file: UploadFile = File(..., description="File to upload"),
    folder_name: str = Form(...),
    metadata: str | None = Form(None, description="Optional JSON object string. Example: {'source':'manual-upload'}"),
):
    return await upload_files_and_record_metadata(files=[file], folder_name=folder_name, metadata=metadata)


@app.post("/chat/query", response_model=ChatQueryResponse)
def chat_query(request: ChatQueryRequest):
    return answer_query(request)
