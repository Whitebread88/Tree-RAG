import os

from google.cloud.sql.connector import Connector
from sqlmodel import create_engine

connector = Connector()


def get_connection():
    return connector.connect(
        os.environ["INSTANCE_CONNECTION_NAME"],
        "pg8000",
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASS"],
        db=os.environ["DB_NAME"],
    )


engine = create_engine("postgresql+pg8000://", creator=get_connection)
