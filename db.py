import os
from google.cloud.sql.connector import Connector, IPTypes
from sqlmodel import create_engine

# Initialize the Connector
connector = Connector()

def get_connection():
    return connector.connect(
        os.environ["INSTANCE_CONNECTION_NAME"],
        "pg8000",
        user=os.environ["DB_USER"],    # This must be your IAM email or SA name
        db=os.environ["DB_NAME"],
        enable_iam_auth=True,          # <--- Activates IAM Authentication
        ip_type=IPTypes.PUBLIC         # Use IPTypes.PRIVATE if using a VPC
    )

# The "postgresql+pg8000://" prefix is required for the pg8000 driver
engine = create_engine("postgresql+pg8000://", creator=get_connection)
