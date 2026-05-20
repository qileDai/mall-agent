"""CLI: ingest FAQ / tickets / product docs into Qdrant."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.core.config import get_settings
from app.db import qdrant as qdb
from app.services.kb_ingest import (
    ingest_directory,
    ingest_path,
    kb_root,
    reindex_document,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _recreate_collection() -> None:
    """Drop and recreate empty collection (full rebuild)."""
    settings = get_settings()
    client = qdb.get_qdrant_client()
    name = settings.qdrant_collection
    if await client.collection_exists(name):
        await client.delete_collection(name)
        logger.warning("deleted collection %s", name)


async def _run(args: argparse.Namespace) -> int:
    """Execute subcommand."""
    get_settings()
    if args.recreate:
        await _recreate_collection()

    if args.delete_doc:
        from app.services.kb_ingest import delete_document_points

        await delete_document_points(args.delete_doc, args.source)
        logger.info("deleted doc_id=%s", args.delete_doc)
        return 0

    if args.reindex_doc:
        if not args.source or not args.text_file:
            logger.error("--reindex-doc requires --source and --text-file")
            return 1
        text = args.text_file.read_text(encoding="utf-8")
        n = await reindex_document(
            args.reindex_doc,
            args.source,
            text,
            version=args.version or "",
        )
        logger.info("reindexed doc_id=%s points=%s", args.reindex_doc, n)
        return 0

    if args.path:
        n = await ingest_path(args.path, replace=not args.no_replace)
        logger.info("ingested %s points from %s", n, args.path)
        return 0

    if args.dir:
        n = await ingest_directory(args.dir, replace=not args.no_replace)
        logger.info("ingested %s points from %s", n, args.dir)
        return 0

    # default: ingest data/kb
    root = kb_root()
    if not root.exists():
        logger.error("KB directory missing: %s (create it and add .jsonl / .md files)", root)
        return 1
    n = await ingest_directory(root, replace=not args.no_replace)
    logger.info("ingested %s points from %s", n, root)
    return 0


def main() -> None:
    """Entry point for ``python -m app.cli.ingest_kb``."""
    parser = argparse.ArgumentParser(description="Ingest KB files into Qdrant (mall_cs_kb)")
    parser.add_argument("--path", type=str, help="Single file: .jsonl / .md / .txt")
    parser.add_argument("--dir", type=str, help="Directory to scan (default: DATA_DIR/kb)")
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Drop collection before ingest (full rebuild; use with --dir or default kb/)",
    )
    parser.add_argument(
        "--no-replace",
        action="store_true",
        help="Do not delete existing points per doc_id before upsert",
    )
    parser.add_argument("--delete-doc", type=str, help="Delete all points for doc_id")
    parser.add_argument("--reindex-doc", type=str, help="Update one doc_id in place")
    parser.add_argument("--source", type=str, help="Source label for --reindex-doc")
    parser.add_argument("--text-file", type=str, help="New content file for --reindex-doc")
    parser.add_argument("--version", type=str, default="", help="Optional version tag in metadata")
    args = parser.parse_args()
    if args.path:
        args.path = __import__("pathlib").Path(args.path)
    if args.dir:
        args.dir = __import__("pathlib").Path(args.dir)
    if args.text_file:
        args.text_file = __import__("pathlib").Path(args.text_file)
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
