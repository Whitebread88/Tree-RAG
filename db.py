import os

from sqlmodel import create_engine

DATABASE_URL = os.environ["DATABASE_URL"]

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=5,
    max_overflow=2,
)


def close_connector() -> None:
    engine.dispose()
