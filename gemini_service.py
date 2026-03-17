import json
import os
from typing import Any

from fastapi import HTTPException
from google.genai import Client, types

HIERARCHY_RESPONSE_SCHEMA = {
    "type": "object",
    "required": ["tree", "file_mappings"],
    "properties": {
        "tree": {
            "type": "array",
            "items": {"$ref": "#/$defs/node"},
        },
        "file_mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["file_id", "node_ids", "confidence"],
                "properties": {
                    "file_id": {"type": "string"},
                    "node_ids": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                },
            },
        },
    },
    "$defs": {
        "node": {
            "type": "object",
            "required": ["node_id", "name", "type", "children"],
            "properties": {
                "node_id": {"type": "string"},
                "name": {"type": "string"},
                "type": {"type": "string"},
                "children": {"type": "array", "items": {"$ref": "#/$defs/node"}},
                "metadata": {"type": "object"},
            },
        }
    },
}


def _parse_json_from_gemini(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"Gemini returned invalid JSON: {exc}") from exc


def generate_hierarchy_with_gemini(folder_name: str, files_payload: list[dict[str, Any]]) -> dict[str, Any]:
    client = Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    prompt = {
        "task": "Create a hierarchical taxonomy of topics/sub-topics from the uploaded files.",
        "folder_name": folder_name,
        "instructions": [
            "Build a best-effort hierarchy from the provided files.",
            "Use node.type values like topic, subtopic, entity, or other.",
            "Every node in tree must contain a unique node_id.",
            "For file_mappings, use file_id values from file_manifest and node_ids from tree.",
        ],
        "file_manifest": [
            {
                "file_id": f["file_id"],
                "file_name": f["file_name"],
                "mime_type": f.get("mime_type"),
                "gcs_path": f["gcs_path"],
            }
            for f in files_payload
        ],
    }

    contents: list[types.Part] = [types.Part.from_text(text=json.dumps(prompt))]
    for file_data in files_payload:
        gcs_path = file_data.get("gcs_path")
        if not gcs_path:
            continue
        contents.append(
            types.Part.from_uri(
                file_uri=gcs_path,
                mime_type=file_data.get("mime_type") or "application/octet-stream",
            )
        )

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            responseMimeType="application/json",
            responseSchema=HIERARCHY_RESPONSE_SCHEMA,
        ),
    )

    if response.parsed and isinstance(response.parsed, dict):
        return response.parsed
    if not response.text:
        raise HTTPException(status_code=502, detail="Gemini returned an empty response")
    return _parse_json_from_gemini(response.text)
