def chunk_text(text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> list[str]:
    cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if not cleaned:
        return []

    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(len(cleaned), start + chunk_size)

        if end < len(cleaned):
            # Back off to the nearest whitespace so we don't split mid-word.
            # Only look within the tail of the chunk so a chunk with no
            # whitespace in its second half still advances.
            search_start = max(start + 1, end - chunk_size // 4)
            boundary = max(
                cleaned.rfind(" ", search_start, end),
                cleaned.rfind("\n", search_start, end),
            )
            if boundary != -1:
                end = boundary

        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(piece)

        if end >= len(cleaned):
            break

        # Guarantee forward progress even if overlap would otherwise pull start back.
        start = max(start + 1, end - chunk_overlap)

    return chunks
