from typing import Annotated

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from sqlmodel import Session, select
from chatbot_service import (
    answer_query,
    delete_conversation,
    get_conversation_messages,
    list_conversations,
)
from db import close_connector, engine
from db_init import ensure_database_schema
from schemas import (
    ChatQueryRequest,
    ChatQueryResponse,
    ConversationListResponse,
    ConversationMessagesResponse,
    FileUploadBatchResponse,
    UserLoginRequest,
    UserResponse,
)
from upload_service import upload_files_and_record_metadata
from user_service import record_user_login

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


@app.get("/upload", include_in_schema=False)
def upload_page():
    """Simple HTML form for testing multi-file uploads in the browser."""
    return HTMLResponse("""<!DOCTYPE html>
<html>
<head><title>Upload Files</title></head>
<body>
  <h2>Upload Files</h2>
  <form action="/files/upload" method="post" enctype="multipart/form-data">
    <label>Files:</label><br>
    <input type="file" name="files" multiple><br><br>
    <label>Folder name:</label><br>
    <input type="text" name="folder_name" required><br><br>
    <label>Metadata (optional JSON):</label><br>
    <input type="text" name="metadata" placeholder='{"source":"manual-upload"}'><br><br>
    <button type="submit">Upload</button>
  </form>
</body>
</html>""")


@app.post("/files/upload", response_model=FileUploadBatchResponse)
async def upload_files(
    files: Annotated[list[UploadFile], File(description="One or more files to upload")],
    folder_name: Annotated[str, Form()],
    metadata: Annotated[str, Form(description="Optional JSON object string")] = "",
):
    return await upload_files_and_record_metadata(
        files=files,
        folder_name=folder_name,
        metadata=metadata or None,
    )


@app.post("/users/login", response_model=UserResponse)
def user_login(request: UserLoginRequest):
    return record_user_login(request)


@app.post("/chat/query", response_model=ChatQueryResponse)
def chat_query(request: ChatQueryRequest):
    return answer_query(request)


@app.get("/chat/conversations", response_model=ConversationListResponse)
def chat_list_conversations(user_id: str):
    return list_conversations(user_id=user_id)


@app.get("/chat/conversations/{conversation_id}", response_model=ConversationMessagesResponse)
def chat_get_conversation(conversation_id: int, user_id: str):
    return get_conversation_messages(user_id=user_id, conversation_id=conversation_id)


@app.delete("/chat/conversations/{conversation_id}", status_code=204)
def chat_delete_conversation(conversation_id: int, user_id: str):
    delete_conversation(user_id=user_id, conversation_id=conversation_id)
    return None
