from typing import Optional
from fastapi import FastAPI, Depends
from sqlmodel import SQLModel, Field, Session, select
from .database import engine, get_session

app = FastAPI()
