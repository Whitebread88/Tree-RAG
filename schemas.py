from pydantic import BaseModel


class IngestResponse(BaseModel):
    folder_id: str
    files_ingested: int
    hierarchy_nodes_created: int
    created_at: str
