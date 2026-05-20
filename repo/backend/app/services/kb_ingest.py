"""Knowledge-base ingestion: FAQ, tickets, product docs → Qdrant chunks."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Sequence

from qdrant_client.http import models as qm

from app.core.config import get_settings
from app.db import qdrant as qdb
from app.services.embedding import embed_texts
from app.services.rag_cache import invalidate_rag_cache

logger = logging.getLogger(__name__)

_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


@dataclass
class KBRecord:
    """One logical document before chunking."""

    doc_id: str
    text: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class KBChunk:
    """One vector point to upsert."""

    point_id: str
    text: str
    metadata: dict[str, Any]


def kb_root() -> Path:
    """Return ``{DATA_DIR}/kb`` path."""
    settings = get_settings()
    root = Path(settings.data_dir)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[2] / root
    return root / "kb"


def make_point_id(source: str, doc_id: str, chunk_index: int) -> str:
    """
    Deterministic point id for upsert-overwrite on document updates.

    Args:
        source: Logical source type (payment_faq, historical_ticket, …).
        doc_id: Stable business document id.
        chunk_index: Zero-based chunk index within the document.

    Returns:
        UUID string accepted by Qdrant.
    """
    key = f"{source}:{doc_id}:{chunk_index}"
    return str(uuid.uuid5(_NAMESPACE, key))


def chunk_text(text: str, chunk_size: int = 480, overlap: int = 80) -> list[str]:
    """
    Split long text into overlapping character chunks (Chinese-friendly).

    Args:
        text: Full document body.
        chunk_size: Max characters per chunk.
        overlap: Overlap between consecutive chunks.

    Returns:
        Non-empty chunk strings.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def records_to_chunks(
    records: Sequence[KBRecord],
    chunk_size: int = 480,
    overlap: int = 80,
) -> list[KBChunk]:
    """
    Turn KB records into point-level chunks with stable ids.

    Args:
        records: Logical documents.
        chunk_size: Chunk size in characters.
        overlap: Chunk overlap.

    Returns:
        List of ``KBChunk`` ready for embedding.
    """
    out: list[KBChunk] = []
    for rec in records:
        parts = chunk_text(rec.text, chunk_size=chunk_size, overlap=overlap)
        for idx, part in enumerate(parts):
            meta = {
                "doc_id": rec.doc_id,
                "source": rec.source,
                **rec.metadata,
            }
            out.append(
                KBChunk(
                    point_id=make_point_id(rec.source, rec.doc_id, idx),
                    text=part,
                    metadata=meta,
                )
            )
    return out


def load_jsonl(path: Path) -> list[KBRecord]:
    """
    Load ``.jsonl`` where each line is a JSON object with ``doc_id``, ``text``, ``source``.

    Optional fields are copied into metadata (``topic``, ``title``, ``version``, etc.).
    """
    records: list[KBRecord] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            row = json.loads(line)
            doc_id = str(row.get("doc_id") or row.get("id") or f"{path.stem}-{line_no}")
            text = str(row.get("text") or row.get("content") or "").strip()
            if not text:
                logger.warning("skip empty line %s:%s", path, line_no)
                continue
            source = str(row.get("source") or path.stem)
            meta = {
                k: v
                for k, v in row.items()
                if k not in {"doc_id", "id", "text", "content", "source"}
            }
            records.append(KBRecord(doc_id=doc_id, text=text, source=source, metadata=meta))
    return records


def load_markdown(path: Path, source: str | None = None) -> list[KBRecord]:
    """Load a single ``.md`` / ``.txt`` file as one document (chunked later)."""
    text = path.read_text(encoding="utf-8").strip()
    doc_id = path.stem
    src = source or "product_doc"
    return [
        KBRecord(
            doc_id=doc_id,
            text=text,
            source=src,
            metadata={"filename": path.name, "format": path.suffix.lstrip(".")},
        )
    ]


def iter_kb_files(root: Path) -> Iterator[Path]:
    """Yield ingestible files under ``root``."""
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".jsonl", ".md", ".txt"}:
            yield path


async def delete_document_points(doc_id: str, source: str | None = None) -> int:
    """
    Remove all Qdrant points for a logical document (before re-ingest).

    Args:
        doc_id: Business document id stored in payload.
        source: Optional source filter.

    Returns:
        1 if delete filter was sent (Qdrant does not always return count).
    """
    settings = get_settings()
    client = qdb.get_qdrant_client()
    if not await client.collection_exists(settings.qdrant_collection):
        return 0
    must = [qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))]
    if source:
        must.append(qm.FieldCondition(key="source", match=qm.MatchValue(value=source)))
    await client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=qm.FilterSelector(filter=qm.Filter(must=must)),
    )
    logger.info("deleted points doc_id=%s source=%s", doc_id, source)
    return 1


async def upsert_chunks(chunks: Sequence[KBChunk]) -> int:
    """
    Embed and upsert chunks into the configured Qdrant collection.

    Args:
        chunks: Point-level chunks.

    Returns:
        Number of points upserted.
    """
    if not chunks:
        return 0
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required for embedding ingestion")
    texts = [c.text for c in chunks]
    vectors = await embed_texts(texts)
    await qdb.ensure_collection(len(vectors[0]))
    client = qdb.get_qdrant_client()
    points = [
        qm.PointStruct(
            id=c.point_id,
            vector=vec,
            payload={"text": c.text, **c.metadata},
        )
        for c, vec in zip(chunks, vectors, strict=True)
    ]
    await client.upsert(collection_name=settings.qdrant_collection, points=points)
    invalidate_rag_cache()
    return len(points)


async def ingest_records(
    records: Sequence[KBRecord],
    *,
    chunk_size: int = 480,
    overlap: int = 80,
    replace: bool = True,
) -> int:
    """
    Ingest logical documents: optionally delete old points per ``doc_id``, then upsert chunks.

    Args:
        records: Documents to ingest.
        chunk_size: Chunk character size.
        overlap: Chunk overlap.
        replace: If True, delete existing points for each ``doc_id`` before upsert.

    Returns:
        Total points upserted.
    """
    if replace:
        seen: set[tuple[str, str]] = set()
        for rec in records:
            key = (rec.source, rec.doc_id)
            if key in seen:
                continue
            seen.add(key)
            await delete_document_points(rec.doc_id, rec.source)
    chunks = records_to_chunks(records, chunk_size=chunk_size, overlap=overlap)
    return await upsert_chunks(chunks)


async def ingest_path(path: Path, *, replace: bool = True) -> int:
    """
    Ingest a single file (``.jsonl``, ``.md``, ``.txt``).

    Args:
        path: File path.
        replace: Delete old points per document before upsert.

    Returns:
        Points upserted.
    """
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        records = load_jsonl(path)
    elif suffix in {".md", ".txt"}:
        records = load_markdown(path)
    else:
        raise ValueError(f"unsupported file type: {path}")
    return await ingest_records(records, replace=replace)


async def ingest_directory(directory: Path | None = None, *, replace: bool = True) -> int:
    """
    Ingest all supported files under ``{DATA_DIR}/kb`` (or given directory).

    Returns:
        Total points upserted across files.
    """
    root = directory or kb_root()
    total = 0
    for path in iter_kb_files(root):
        n = await ingest_path(path, replace=replace)
        logger.info("ingested %s -> %s points", path, n)
        total += n
    return total


async def reindex_document(doc_id: str, source: str, text: str, **metadata: Any) -> int:
    """
    Update one document: delete its points by ``doc_id``, then upsert new chunks.

    Use this when FAQ / ticket / policy text changes.

    Args:
        doc_id: Stable document id.
        source: Source label (payment_faq, historical_ticket, product_doc).
        text: New full text.
        **metadata: Extra payload fields (topic, version, updated_at, …).

    Returns:
        Points upserted.
    """
    rec = KBRecord(doc_id=doc_id, text=text, source=source, metadata=dict(metadata))
    return await ingest_records([rec], replace=True)
