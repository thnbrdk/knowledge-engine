"""LanceDB vector store wrapper with fastembed embeddings."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import lancedb
import numpy as np
from fastembed import TextEmbedding

from .crawler import get_top_level_category

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_EMBEDDING_DIM = 384


@dataclass
class VectorResult:
    chunk_id: str
    content: str
    file_path: str
    category: str
    title: str
    heading_path: str
    distance: float


class VectorStore:
    def __init__(self, lance_path: Path) -> None:
        lance_path.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(lance_path))
        self._embedder = TextEmbedding(_EMBEDDING_MODEL)
        self._tables: dict[str, lancedb.table.Table] = {}

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts."""
        return [vec.tolist() for vec in self._embedder.embed(texts)]

    def _get_table(self, category: str) -> lancedb.table.Table:
        """Get or create a table for the top-level category."""
        top = get_top_level_category(category)
        if top in self._tables:
            return self._tables[top]

        existing = self._db.table_names()
        if top in existing:
            tbl = self._db.open_table(top)
        else:
            # Create empty table with schema
            tbl = self._db.create_table(
                top,
                data=[{
                    "chunk_id": "__schema__",
                    "content": "",
                    "file_path": "",
                    "category": "",
                    "title": "",
                    "heading_path": "",
                    "vector": [0.0] * _EMBEDDING_DIM,
                }],
            )
            # Remove the schema-placeholder row
            tbl.delete('chunk_id = "__schema__"')

        self._tables[top] = tbl
        return tbl

    def upsert_chunks(
        self,
        category: str,
        chunks: list[dict],
    ) -> None:
        """Upsert chunks into the vector store.

        Each chunk dict must have: chunk_id, content, file_path, category, title, heading_path
        """
        if not chunks:
            return

        table = self._get_table(category)

        texts = [c["content"] for c in chunks]
        vectors = self._embed(texts)

        rows = []
        for c, vec in zip(chunks, vectors):
            rows.append({
                "chunk_id": c["chunk_id"],
                "content": c["content"],
                "file_path": c["file_path"],
                "category": c["category"],
                "title": c["title"],
                "heading_path": " > ".join(c["heading_path"]) if c["heading_path"] else "",
                "vector": vec,
            })

        # Delete existing chunks with same IDs (upsert)
        ids = [c["chunk_id"] for c in chunks]
        for chunk_id in ids:
            safe_id = chunk_id.replace("'", "''")
            try:
                table.delete(f"chunk_id = '{safe_id}'")
            except Exception:
                pass

        table.add(rows)

    def delete_by_file(self, category: str, file_path: str) -> None:
        """Delete all chunks for a given file."""
        try:
            table = self._get_table(category)
            safe_path = file_path.replace("'", "''")
            table.delete(f"file_path = '{safe_path}'")
        except Exception:
            logger.debug("No chunks found for file %s in category %s", file_path, category)

    def delete_by_file_all(self, file_path: str) -> None:
        """Delete all chunks for a file across all vector tables."""
        safe_path = file_path.replace("'", "''")
        for name in self._db.table_names():
            try:
                table = self._db.open_table(name)
                table.delete(f"file_path = '{safe_path}'")
            except Exception:
                logger.debug("No chunks found for file %s in table %s", file_path, name)

    def delete_category(self, category: str) -> None:
        """Delete all data for a category."""
        top = get_top_level_category(category)
        try:
            if category != top and category != ".":
                # Subcategory: delete only matching rows
                table = self._get_table(category)
                safe_cat = category.replace("'", "''")
                table.delete(f"category = '{safe_cat}'")
            else:
                # Top-level: drop the whole table
                if top in self._db.table_names():
                    self._db.drop_table(top)
                self._tables.pop(top, None)
        except Exception:
            logger.debug("Error deleting category %s", category)

    def search(
        self,
        query: str,
        category: str | None = None,
        n_results: int = 10,
    ) -> list[VectorResult]:
        """Semantic search across tables."""
        if not query.strip():
            return []

        query_vec = self._embed([query])[0]
        results: list[VectorResult] = []

        if category:
            tables_to_search = [self._get_table(category)]
            where_filter = f"category = '{category.replace(chr(39), chr(39)*2)}'" if "/" in category else None
        else:
            tables_to_search = []
            for name in self._db.table_names():
                tables_to_search.append(self._db.open_table(name))
            where_filter = None

        for table in tables_to_search:
            try:
                if table.count_rows() == 0:
                    continue
                search_query = table.search(query_vec).limit(n_results)
                if where_filter:
                    search_query = search_query.where(where_filter)
                rows = search_query.to_list()
            except Exception:
                continue

            for row in rows:
                results.append(
                    VectorResult(
                        chunk_id=row.get("chunk_id", ""),
                        content=row.get("content", ""),
                        file_path=row.get("file_path", ""),
                        category=row.get("category", ""),
                        title=row.get("title", ""),
                        heading_path=row.get("heading_path", ""),
                        distance=row.get("_distance", 0.0),
                    )
                )

        # Sort by distance (lower = more similar)
        results.sort(key=lambda r: r.distance)
        return results[:n_results]

    def search_similar(self, file_path: str, category: str, n_results: int = 5) -> list[VectorResult]:
        """Find documents similar to a given file."""
        try:
            table = self._get_table(category)
            safe_path = file_path.replace("'", "''")
            file_rows = table.search().where(f"file_path = '{safe_path}'").limit(1).to_list()
        except Exception:
            return []

        if not file_rows:
            return []

        # Use the first chunk's content as the query
        query_text = file_rows[0].get("content", "")
        if not query_text:
            return []

        query_vec = self._embed([query_text])[0]

        try:
            if table.count_rows() <= 1:
                return []
            rows = table.search(query_vec).limit(n_results + 10).to_list()
        except Exception:
            return []

        results: list[VectorResult] = []
        seen_files: set[str] = set()

        for row in rows:
            fp = row.get("file_path", "")
            if fp == file_path or fp in seen_files:
                continue
            seen_files.add(fp)

            results.append(
                VectorResult(
                    chunk_id=row.get("chunk_id", ""),
                    content=row.get("content", ""),
                    file_path=fp,
                    category=row.get("category", ""),
                    title=row.get("title", ""),
                    heading_path=row.get("heading_path", ""),
                    distance=row.get("_distance", 0.0),
                )
            )

            if len(results) >= n_results:
                break

        return results

    def compact(self) -> dict[str, int]:
        """Compact all tables and clean up old versions.

        Returns dict with bytes_removed per table.
        """
        from datetime import timedelta

        stats: dict[str, int] = {}
        for name in self._db.table_names():
            try:
                table = self._db.open_table(name)
                table.compact_files()
                cleaned = table.cleanup_old_versions(
                    older_than=timedelta(minutes=5),
                    delete_unverified=True,
                )
                stats[name] = cleaned.bytes_removed
                logger.info("Compacted table %s: removed %d bytes", name, cleaned.bytes_removed)
            except Exception:
                logger.warning("Failed to compact table %s", name, exc_info=True)
                stats[name] = 0
        self._tables.clear()
        return stats

    def close(self) -> None:
        """Reset caches."""
        self._tables.clear()
