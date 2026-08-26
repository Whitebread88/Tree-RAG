from dataclasses import dataclass


@dataclass
class DocumentSegment:
    """A contiguous run of document text sharing one page and section heading.

    `heading` is the breadcrumb of enclosing headings (for example
    "Refund Policy > Annual Plans"), or None when the source format gave us
    no structure to work with.
    """

    page_number: int | None
    text: str
    heading: str | None = None


@dataclass
class TextChunk:
    text: str
    page_number: int | None
    char_offset_start: int
    char_offset_end: int
    heading: str | None = None


def chunk_text(text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> list[str]:
    segments = [DocumentSegment(page_number=None, text=text)]
    return [c.text for c in chunk_segments(segments, chunk_size=chunk_size, chunk_overlap=chunk_overlap)]


def chunk_segments(
    segments: list[DocumentSegment],
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
) -> list[TextChunk]:
    """Chunk document segments without crossing page or section boundaries.

    char_offset_{start,end} are positions in the concatenated source text
    (segments joined by a single newline) so they can be used to highlight
    the original document later, and to detect overlapping chunks at
    retrieval time.
    """
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    chunks: list[TextChunk] = []
    global_cursor = 0  # tracks position in the concatenated source text

    for index, segment in enumerate(segments):
        cleaned = "\n".join(line.strip() for line in segment.text.splitlines() if line.strip())
        segment_global_start = global_cursor

        if cleaned:
            chunks.extend(
                _chunks_for_segment(
                    cleaned=cleaned,
                    page_number=segment.page_number,
                    heading=segment.heading,
                    segment_global_start=segment_global_start,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                )
            )

        global_cursor += len(cleaned)
        if index < len(segments) - 1:
            global_cursor += 1  # the joining newline between segments

    return chunks


def _chunks_for_segment(
    cleaned: str,
    page_number: int | None,
    heading: str | None,
    segment_global_start: int,
    chunk_size: int,
    chunk_overlap: int,
) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    start = 0
    length = len(cleaned)

    while start < length:
        end = min(length, start + chunk_size)

        if end < length:
            # Back off to the nearest whitespace so we don't split mid-word.
            search_start = max(start + 1, end - chunk_size // 4)
            boundary = max(
                cleaned.rfind(" ", search_start, end),
                cleaned.rfind("\n", search_start, end),
            )
            if boundary != -1:
                end = boundary

        piece = cleaned[start:end]
        stripped = piece.strip()
        if stripped:
            leading_ws = len(piece) - len(piece.lstrip())
            trailing_ws = len(piece) - len(piece.rstrip())
            chunk_global_start = segment_global_start + start + leading_ws
            chunk_global_end = segment_global_start + end - trailing_ws
            chunks.append(
                TextChunk(
                    text=stripped,
                    page_number=page_number,
                    char_offset_start=chunk_global_start,
                    char_offset_end=chunk_global_end,
                    heading=heading,
                )
            )

        if end >= length:
            break
        start = max(start + 1, end - chunk_overlap)

    return chunks
