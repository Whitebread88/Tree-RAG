import asyncio
import json

from db_init import ensure_database_schema
from processing_service import process_uploaded_files
from schemas import ProcessFilesRequest


async def run() -> None:
    ensure_database_schema()
    response = await process_uploaded_files(ProcessFilesRequest())
    print(json.dumps(response.model_dump(), default=str))


if __name__ == "__main__":
    asyncio.run(run())
