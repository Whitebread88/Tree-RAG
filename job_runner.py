import asyncio
import json
import logging

from db import close_connector
from db_init import ensure_database_schema
from processing_service import process_uploaded_files
from schemas import ProcessFilesRequest

logging.basicConfig(level=logging.INFO)


async def run() -> None:
    logging.info("Cloud Run job runner started")
    try:
        ensure_database_schema()
        response = await process_uploaded_files(ProcessFilesRequest())
        logging.info("Cloud Run job runner finished")
        print(json.dumps(response.model_dump(), default=str))
    finally:
        close_connector()


if __name__ == "__main__":
    asyncio.run(run())
