from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, UploadFile
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
    FolderProcessingStatusResponse,
    UserLoginRequest,
    UserResponse,
)
from upload_service import get_folder_processing_status, upload_files_and_record_metadata
from user_service import record_user_login
from auth import CurrentUser, auth_settings, require_user

app = FastAPI()
api = APIRouter(dependencies=[Depends(require_user)])


@app.on_event("startup")
def startup() -> None:
    auth_settings()
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
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error"})


@app.get("/")
def read_root():
    return {"message": "Cloud Run is active"}


@api.get("/upload", include_in_schema=False)
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
    <label>User ID:</label><br>
    <input type="text" name="id" required><br><br>
    <label>Metadata (optional JSON):</label><br>
    <input type="text" name="metadata" placeholder='{"source":"manual-upload"}'><br><br>
    <button type="submit">Upload</button>
  </form>
</body>
</html>""")


@api.post("/files/upload", response_model=FileUploadBatchResponse)
async def upload_files(
    user: CurrentUser,
    files: Annotated[list[UploadFile], File(description="One or more files to upload")],
    folder_name: Annotated[str, Form()],
    id: Annotated[str, Form(description="Authenticated user ID; matches UserLoginRequest.id")],
    metadata: Annotated[str, Form(description="Optional JSON object string")] = "",
):
    user.require_matching_id(id)
    return await upload_files_and_record_metadata(
        files=files,
        folder_name=folder_name,
        user_id=user.id,
        metadata=metadata or None,
    )


@api.get("/files/processing-status", response_model=FolderProcessingStatusResponse)
def get_files_processing_status(id: str, folder_name: str, user: CurrentUser):
    user.require_matching_id(id)
    return get_folder_processing_status(user_id=user.id, folder_name=folder_name)


@api.post("/users/login", response_model=UserResponse)
def user_login(request: UserLoginRequest, user: CurrentUser):
    user.require_matching_id(request.id)
    if not user.name:
        raise HTTPException(status_code=403, detail="Authenticated name is required")
    return record_user_login(UserLoginRequest(id=user.id, name=user.name))


@api.post("/chat/query", response_model=ChatQueryResponse)
def chat_query(request: ChatQueryRequest, user: CurrentUser):
    user.require_matching_id(request.user_id)
    return answer_query(request.model_copy(update={"user_id": user.id}))


@api.get("/chat/conversations", response_model=ConversationListResponse)
def chat_list_conversations(user_id: str, user: CurrentUser):
    user.require_matching_id(user_id)
    return list_conversations(user_id=user.id)


@api.get("/chat/conversations/{conversation_id}", response_model=ConversationMessagesResponse)
def chat_get_conversation(conversation_id: int, user_id: str, user: CurrentUser):
    user.require_matching_id(user_id)
    return get_conversation_messages(user_id=user.id, conversation_id=conversation_id)


@api.delete("/chat/conversations/{conversation_id}", status_code=204)
def chat_delete_conversation(conversation_id: int, user_id: str, user: CurrentUser):
    user.require_matching_id(user_id)
    delete_conversation(user_id=user.id, conversation_id=conversation_id)
    return None


app.include_router(api)
