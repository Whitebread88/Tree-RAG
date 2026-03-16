import os
from fastapi import FastAPI
from sqlalchemy import text
from sqlmodel import create_engine
from google.cloud.sql.connector import Connector

# 1. Setup the Cloud SQL Connector
connector = Connector()

def get_connection():
    return connector.connect(
        os.environ["INSTANCE_CONNECTION_NAME"],
        "pg8000",
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
        db=os.environ["DB_NAME"]
    )

# 2. Create the Engine
engine = create_engine("postgresql+pg8000://", creator=get_connection)

app = FastAPI()

@app.get("/health")
def health_check():
    try:
        # This physically tests the connection to your Postgres instance
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "connected", "database": "PostgreSQL is reachable"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/")
def read_root():
    return {"message": "Cloud Run is active"}
