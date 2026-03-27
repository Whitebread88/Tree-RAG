import tempfile
from pathlib import Path


async def extract_text_with_raganything(file_name: str, file_bytes: bytes) -> str:
    try:
        from raganything import RAGAnything
    except Exception as exc:  # pragma: no cover - optional dependency/runtime configuration
        raise RuntimeError("raganything library is not available") from exc

    suffix = Path(file_name).suffix or ".bin"

    with tempfile.TemporaryDirectory(prefix="raganything-") as tmp_dir:
        input_path = Path(tmp_dir) / f"input{suffix}"
        output_dir = Path(tmp_dir) / "output"
        input_path.write_bytes(file_bytes)

        rag = RAGAnything()
        try:
            result = await rag.process_document_complete(
                file_path=str(input_path),
                output_dir=str(output_dir),
                parse_method="auto",
            )
            extracted = _extract_text_from_result(result)
            if extracted:
                return extracted

            markdown_files = list(output_dir.rglob("*.md")) + list(output_dir.rglob("*.txt"))
            if markdown_files:
                return "\n\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in markdown_files)

            raise RuntimeError("raganything did not produce parseable text output")
        finally:
            await rag.finalize_storages()


def _extract_text_from_result(result: object) -> str:
    if not isinstance(result, dict):
        return ""

    if isinstance(result.get("content"), str):
        return result["content"]

    content_list = result.get("content_list")
    if isinstance(content_list, list):
        fragments: list[str] = []
        for item in content_list:
            if isinstance(item, dict):
                for key in ("text", "content", "markdown"):
                    value = item.get(key)
                    if isinstance(value, str) and value.strip():
                        fragments.append(value.strip())
                        break
            elif isinstance(item, str) and item.strip():
                fragments.append(item.strip())
        return "\n".join(fragments)

    return ""
