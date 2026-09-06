"""Retrieval filtering tests.

_build_context is pure logic over query rows, so these exercise the real
thresholding and dedup without a database or an embedding call. Rows are
(chunk, uploaded_file, distance) triples, best-first, as pgvector returns them.
"""
import itertools

import chatbot_service  # noqa: E402
from chatbot_service import _build_context  # noqa: E402


class _Chunk:
    def __init__(self, uploaded_file_id=1, chunk_index=0, text="body", start=0, end=100):
        self.uploaded_file_id = uploaded_file_id
        self.chunk_index = chunk_index
        self.chunk_text = text
        self.context_header = "folder > file.pdf"
        self.page_number = 1
        self.char_offset_start = start
        self.char_offset_end = end


class _File:
    def __init__(self, file_id=1):
        self.id = file_id
        self.original_file_name = "file.pdf"
        self.folder_name = "folder"


_row_counter = itertools.count()


def _row(similarity, chunk=None, file_id=1):
    """A result row at the given cosine similarity (distance = 1 - similarity).

    Auto-generated chunks get non-overlapping offsets so dedup stays out of the
    way; tests that care about dedup pass their own chunks.
    """
    if chunk is None:
        n = next(_row_counter)
        chunk = _Chunk(uploaded_file_id=file_id, chunk_index=n, start=n * 10_000, end=n * 10_000 + 100)
    return (chunk, _File(file_id), 1 - similarity)


# --- defaults ---------------------------------------------------------------


def test_default_threshold_clears_the_unrelated_band():
    """gemini-embedding-001 scores unrelated text around 0.3-0.45, so the floor
    has to sit above that band to filter anything at all."""
    assert chatbot_service._DEFAULT_SIMILARITY_THRESHOLD == 0.5


def test_default_top_k_pulls_a_generous_candidate_set():
    assert chatbot_service._DEFAULT_TOP_K == 50


def test_defaults_are_env_overridable(monkeypatch):
    monkeypatch.setenv("RAG_TOP_K", "80")
    monkeypatch.setenv("RAG_SIMILARITY_THRESHOLD", "0.65")
    import importlib

    reloaded = importlib.reload(chatbot_service)
    try:
        assert reloaded._DEFAULT_TOP_K == 80
        assert reloaded._DEFAULT_SIMILARITY_THRESHOLD == 0.65
    finally:
        monkeypatch.undo()
        importlib.reload(chatbot_service)


def test_default_top_k_is_within_the_schema_bound():
    """The API caps top_k at 100; a server default above it would be rejected
    the moment a caller passed it back explicitly."""
    from schemas import ChatQueryRequest

    field = ChatQueryRequest.model_fields["top_k"]
    upper = next(m.le for m in field.metadata if hasattr(m, "le"))
    assert chatbot_service._DEFAULT_TOP_K <= upper


# --- thresholding -----------------------------------------------------------


def test_chunks_below_threshold_are_dropped():
    rows = [_row(0.72), _row(0.55), _row(0.41), _row(0.33)]
    context, blocks = _build_context(rows=rows, threshold=0.5)

    assert len(context) == 2
    assert len(blocks) == 2
    assert [round(c.similarity_score, 2) for c in context] == [0.72, 0.55]


def test_everything_below_threshold_yields_no_context():
    """The caller turns an empty result into _NO_CONTEXT_ANSWER rather than
    answering from weak matches."""
    rows = [_row(0.44), _row(0.31)]
    context, blocks = _build_context(rows=rows, threshold=0.5)

    assert context == []
    assert blocks == []


def test_threshold_is_inclusive_at_the_boundary():
    context, _ = _build_context(rows=[_row(0.5)], threshold=0.5)
    assert len(context) == 1


def test_negative_similarity_is_clamped_to_zero():
    chunk, file_, _ = _row(0.0)
    context, _ = _build_context(rows=[(chunk, file_, 1.4)], threshold=0.0)
    assert context[0].similarity_score == 0.0


# --- dedup ------------------------------------------------------------------


def test_overlapping_chunks_from_the_same_file_are_deduped():
    """Ingest overlaps its sliding window by design, so adjacent chunks cover
    mostly the same text; without dedup they crowd out distinct passages."""
    first = _Chunk(chunk_index=0, start=0, end=1000)
    second = _Chunk(chunk_index=1, start=800, end=1800)  # 200/1000 overlap
    heavy = _Chunk(chunk_index=2, start=50, end=1050)  # 950/1000 overlap

    context, _ = _build_context(
        rows=[_row(0.8, first), _row(0.75, heavy), _row(0.7, second)], threshold=0.5
    )

    kept = [c.chunk_index for c in context]
    assert 0 in kept and 1 in kept
    assert 2 not in kept, "near-identical window should have been deduped"


def test_same_offsets_in_different_files_are_both_kept():
    a = _Chunk(uploaded_file_id=1, chunk_index=0, start=0, end=1000)
    b = _Chunk(uploaded_file_id=2, chunk_index=0, start=0, end=1000)

    context, _ = _build_context(
        rows=[_row(0.8, a, file_id=1), _row(0.78, b, file_id=2)], threshold=0.5
    )
    assert len(context) == 2


# --- context budget ---------------------------------------------------------


def test_context_budget_stops_adding_blocks(monkeypatch):
    monkeypatch.setattr(chatbot_service, "_MAX_CONTEXT_CHARS", 200)
    rows = [
        _row(0.9 - i * 0.01, _Chunk(chunk_index=i, text="x" * 150, start=i * 5000, end=i * 5000 + 150))
        for i in range(5)
    ]
    context, _ = _build_context(rows=rows, threshold=0.5)

    assert 0 < len(context) < 5


def test_first_chunk_is_kept_even_when_it_exceeds_the_budget(monkeypatch):
    monkeypatch.setattr(chatbot_service, "_MAX_CONTEXT_CHARS", 10)
    context, _ = _build_context(rows=[_row(0.9, _Chunk(text="x" * 5000))], threshold=0.5)

    assert len(context) == 1, "the LLM must always get something to reason over"


# --- ordering ---------------------------------------------------------------


def test_results_stay_in_best_first_order():
    rows = [_row(0.9 - i * 0.1, _Chunk(chunk_index=i, start=i * 5000, end=i * 5000 + 100)) for i in range(3)]
    context, _ = _build_context(rows=rows, threshold=0.5)

    scores = [c.similarity_score for c in context]
    assert scores == sorted(scores, reverse=True)


def test_empty_result_set_is_handled():
    assert _build_context(rows=[], threshold=0.5) == ([], [])
