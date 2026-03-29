from sqlmodel import SQLModel

from db import engine


def ensure_database_schema() -> None:
    SQLModel.metadata.create_all(engine)
