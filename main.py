from typing import Optional
from fastapi import FastAPI, Depends
from sqlmodel import SQLModel, Session, select
from contextlib import asynccontextmanager
from .database import engine, get_session

@asynccontextmanager
async def lifespan(app: FastAPI):
    # This triggers the connection to Cloud SQL immediately on startup
    # If this fails, you will see a CLEAR error in the Cloud Run logs
    SQLModel.metadata.create_all(engine)
    yield

app = FastAPI()

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
