import os
from fastapi import FastAPI
from sqlmodel import SQLModel
from database import engine 

app = FastAPI()

@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)

@app.get("/health")
def health():
    return {"status": "ok"}
