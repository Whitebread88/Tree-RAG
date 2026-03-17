import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy import text

from db import engine
from gcs_service import get_storage_bucket
from gemini_service import generate_hierarchy_with_gemini

UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1MB


def _collect_hierarchy_nodes(
    folder_id: str,
    nodes: list[dict[str, Any]],
    parent_id: str | None = None,
    model_node_to_db_node: dict[str, str] | None = None,
    node_rows: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    node_map = model_node_to_db_node if model_node_to_db_node is not None else {}
    rows = node_rows if node_rows is not None else []

    for idx, node in enumerate(nodes):
        db_node_id = str(uuid.uuid4())
        model_node_id = str(node.get("node_id") or uuid.uuid4())

        rows.append(
            {
                "id": db_node_id,
                "folder_id": folder_id,
                "parent_id": parent_id,
                "node_name": node.get("name", "Unnamed"),
                "node_type": node.get("type", "other"),
                "node_order": idx,
                "metadata": json.dumps({"model_node_id": model_node_id, **node.get("metadata", {})}),
            }
        )

        node_map[model_node_id] = db_node_id
        children = node.get("children", [])
        if isinstance(children, list) and children:
            _collect_hierarchy_nodes(folder_id, children, db_node_id, node_map, rows)

    return node_map, rows


def _stream_upload_to_gcs(bucket, object_name: str, uploaded: UploadFile) -> int:
    uploaded.file.seek(0)
    bytes_written = 0

    blob = bucket.blob(object_name)
    with blob.open("wb", content_type=uploaded.content_type) as out_file:
        while True:
            chunk = uploaded.file.read(UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            out_file.write(chunk)
            bytes_written += len(chunk)

    return bytes_written


def _namespace_hierarchy_ids(file_id: str, hierarchy: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tree_nodes = hierarchy.get("tree", [])
    file_mappings = hierarchy.get("file_mappings", [])

    old_to_new_node_ids: dict[str, str] = {}

    def _rewrite_node_ids(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            original_node_id = str(node.get("node_id") or uuid.uuid4())
            namespaced_node_id = f"{file_id}:{original_node_id}"
            old_to_new_node_ids[original_node_id] = namespaced_node_id
            node["node_id"] = namespaced_node_id

            children = node.get("children", [])
            if isinstance(children, list) and children:
                _rewrite_node_ids(children)

    _rewrite_node_ids(tree_nodes)

    normalized_mappings: list[dict[str, Any]] = []
    for mapping in file_mappings:
        node_ids = []
        for original_node_id in mapping.get("node_ids", []):
            new_node_id = old_to_new_node_ids.get(str(original_node_id))
            if new_node_id:
                node_ids.append(new_node_id)

        normalized_mappings.append(
            {
                "file_id": file_id,
                "node_ids": node_ids,
                "confidence": mapping.get("confidence", 0.0),
            }
        )

    return tree_nodes, normalized_mappings


def _cleanup_uploaded_objects(bucket, object_names: list[str]) -> None:
    for object_name in object_names:
        try:
            bucket.blob(object_name).delete()
        except Exception:
            # Best-effort cleanup; avoid masking original failure.
            pass


async def ingest_files_and_build_hierarchy(
    files: list[UploadFile],
    folder_name: str,
    metadata: str | None,
) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")

    parsed_metadata: dict[str, Any] = {}
    if metadata:
        try:
            parsed_metadata = json.loads(metadata)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="metadata must be valid JSON") from exc

    folder_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_folder = folder_name.strip().replace(" ", "_")
    gcs_prefix = f"ingestions/{safe_folder}/{timestamp}-{folder_id}"

    bucket = get_storage_bucket()
    uploaded_object_names: list[str] = []

    file_rows: list[dict[str, Any]] = []
    all_tree_nodes: list[dict[str, Any]] = []
    all_file_mappings: list[dict[str, Any]] = []

    try:
        for uploaded in files:
            file_id = str(uuid.uuid4())
            object_name = f"{gcs_prefix}/{file_id}-{uploaded.filename}"
            uploaded_object_names.append(object_name)

            try:
                size_bytes = _stream_upload_to_gcs(bucket, object_name, uploaded)
            finally:
                await uploaded.close()

            file_row = {
                "id": file_id,
                "original_file_name": uploaded.filename,
                "mime_type": uploaded.content_type,
                "size_bytes": size_bytes,
                "gcs_path": f"gs://{bucket.name}/{object_name}",
                "metadata": json.dumps({}),
            }
            file_rows.append(file_row)

            single_file_payload = {
                "file_id": file_id,
                "file_name": uploaded.filename,
                "mime_type": uploaded.content_type,
                "gcs_path": file_row["gcs_path"],
            }

            # One Gemini request per file.
            hierarchy = generate_hierarchy_with_gemini(folder_name, [single_file_payload])
            file_tree_nodes, file_mappings = _namespace_hierarchy_ids(file_id, hierarchy)
            all_tree_nodes.extend(file_tree_nodes)
            all_file_mappings.extend(file_mappings)

        model_node_to_db_node, hierarchy_node_rows = _collect_hierarchy_nodes(folder_id, all_tree_nodes)

        file_ids = {r["id"] for r in file_rows}
        link_rows: list[dict[str, Any]] = []
        for mapping in all_file_mappings:
            file_id = mapping.get("file_id")
            if file_id not in file_ids:
                continue

            for model_node_id in mapping.get("node_ids", []):
                node_id = model_node_to_db_node.get(model_node_id)
                if not node_id:
                    continue
                link_rows.append(
                    {
                        "id": str(uuid.uuid4()),
                        "file_id": file_id,
                        "node_id": node_id,
                        "confidence": mapping.get("confidence", 0.0),
                        "metadata": json.dumps({"model_node_id": model_node_id}),
                    }
                )

        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO ingestion_folders (id, folder_name, gcs_prefix, metadata)
                    VALUES (:id, :folder_name, :gcs_prefix, CAST(:metadata AS JSONB))
                    """
                ),
                {
                    "id": folder_id,
                    "folder_name": folder_name,
                    "gcs_prefix": gcs_prefix,
                    "metadata": json.dumps(parsed_metadata),
                },
            )

            if file_rows:
                conn.execute(
                    text(
                        """
                        INSERT INTO ingestion_files
                        (id, folder_id, original_file_name, mime_type, size_bytes, gcs_path, metadata)
                        VALUES (:id, :folder_id, :original_file_name, :mime_type, :size_bytes, :gcs_path, CAST(:metadata AS JSONB))
                        """
                    ),
                    [{**row, "folder_id": folder_id} for row in file_rows],
                )

            if hierarchy_node_rows:
                conn.execute(
                    text(
                        """
                        INSERT INTO hierarchy_nodes (id, folder_id, parent_id, node_name, node_type, node_order, metadata)
                        VALUES (:id, :folder_id, :parent_id, :node_name, :node_type, :node_order, CAST(:metadata AS JSONB))
                        """
                    ),
                    hierarchy_node_rows,
                )

            if link_rows:
                conn.execute(
                    text(
                        """
                        INSERT INTO file_hierarchy_links (id, file_id, node_id, confidence, metadata)
                        VALUES (:id, :file_id, :node_id, :confidence, CAST(:metadata AS JSONB))
                        """
                    ),
                    link_rows,
                )

    except Exception:
        _cleanup_uploaded_objects(bucket, uploaded_object_names)
        raise

    return {
        "folder_id": folder_id,
        "files_ingested": len(file_rows),
        "hierarchy_nodes_created": len(model_node_to_db_node),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
