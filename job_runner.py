import asyncio
import json
import logging

from db_init import ensure_database_schema
from processing_service import process_uploaded_files
from schemas import ProcessFilesRequest

logging.basicConfig(level=logging.INFO)


async def run() -> None:
    logging.info("Cloud Run job runner started")
    ensure_database_schema()
    response = await process_uploaded_files(ProcessFilesRequest())
    logging.info("Cloud Run job runner finished")
    print(json.dumps(response.model_dump(), default=str))


if __name__ == "__main__":
    asyncio.run(run())
