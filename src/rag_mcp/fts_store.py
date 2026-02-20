"""SQLite FTS5 full-text search store."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class FTSResult:
    file_path: str
    category: str
    title: str
    snippet: str
    rank: float


class FTSStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        # Verify FTS5 is available
        try:
            self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_check USING fts5(x)"
            )
            self._conn.execute("DROP TABLE IF EXISTS _fts5_check")
        except sqlite3.OperationalError as e:
            raise RuntimeError(
                "SQLite FTS5 extension is not available. "
                "Please use a Python build with FTS5 support."
            ) from e

        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                file_hash TEXT NOT NULL DEFAULT '',
                last_modified REAL NOT NULL DEFAULT 0,
                date TEXT NOT NULL DEFAULT '',
                author TEXT NOT NULL DEFAULT '',
                approved_by TEXT NOT NULL DEFAULT '',
                in_review_by TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                revision INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS document_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                revision INTEGER NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                date TEXT NOT NULL DEFAULT '',
                author TEXT NOT NULL DEFAULT '',
                approved_by TEXT NOT NULL DEFAULT '',
                in_review_by TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                categories TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(file_path, revision)
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                title,
                content,
                content='documents',
                content_rowid='id',
                tokenize='porter unicode61'
            );

            -- Triggers to keep FTS in sync with documents table
            CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
                INSERT INTO documents_fts(rowid, title, content)
                VALUES (new.id, new.title, new.content);
            END;

            CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, title, content)
                VALUES ('delete', old.id, old.title, old.content);
            END;

            CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, title, content)
                VALUES ('delete', old.id, old.title, old.content);
                INSERT INTO documents_fts(rowid, title, content)
                VALUES (new.id, new.title, new.content);
            END;
        """)
        self._conn.commit()

        # Migrate old schemas before creating indexes that depend on new column names
        self._migrate_schema()

        # Create indexes after migration (these reference 'status' which may have been renamed from 'quality')
        self._conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_doc_revisions_file_rev
                ON document_revisions(file_path, revision DESC);
            CREATE INDEX IF NOT EXISTS idx_doc_revisions_status
                ON document_revisions(status, file_path, revision DESC);
        """)
        self._conn.commit()

    def _migrate_schema(self) -> None:
        """Add metadata columns if they don't exist (for existing DBs)."""
        cursor = self._conn.execute("PRAGMA table_info(documents)")
        existing_cols = {row[1] for row in cursor.fetchall()}
        new_cols = {
            "date": "TEXT NOT NULL DEFAULT ''",
            "author": "TEXT NOT NULL DEFAULT ''",
            "approved_by": "TEXT NOT NULL DEFAULT ''",
            "in_review_by": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT 'draft'",
            "revision": "INTEGER NOT NULL DEFAULT 1",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
            "categories": "TEXT NOT NULL DEFAULT '[]'",
        }
        for col, typedef in new_cols.items():
            if col not in existing_cols:
                self._conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {typedef}")

        # Migrate old column names from previous schema
        # Case 1: old DB with only 'quality' column → rename to 'status'
        # Case 2: transition DB with both 'quality' and 'status' → copy data, drop old
        if "quality" in existing_cols:
            if "status" not in existing_cols:
                self._conn.execute("ALTER TABLE documents RENAME COLUMN quality TO status")
            else:
                # Both exist: ensure status has the quality data, then drop old column
                self._conn.execute("UPDATE documents SET status = quality WHERE status = 'draft' AND quality != 'draft'")
                self._conn.execute("ALTER TABLE documents DROP COLUMN quality")
        if "verified_by" in existing_cols:
            if "approved_by" not in existing_cols:
                self._conn.execute("ALTER TABLE documents RENAME COLUMN verified_by TO approved_by")
            else:
                self._conn.execute("UPDATE documents SET approved_by = verified_by WHERE approved_by = '' AND verified_by != ''")
                self._conn.execute("ALTER TABLE documents DROP COLUMN verified_by")
        if "flagged_by" in existing_cols:
            if "in_review_by" not in existing_cols:
                self._conn.execute("ALTER TABLE documents RENAME COLUMN flagged_by TO in_review_by")
            else:
                self._conn.execute("UPDATE documents SET in_review_by = flagged_by WHERE in_review_by = '' AND flagged_by != ''")
                self._conn.execute("ALTER TABLE documents DROP COLUMN flagged_by")

        # Migrate revisions table column names
        rev_cursor = self._conn.execute("PRAGMA table_info(document_revisions)")
        rev_cols = {row[1] for row in rev_cursor.fetchall()}
        if "quality" in rev_cols:
            if "status" not in rev_cols:
                self._conn.execute("ALTER TABLE document_revisions RENAME COLUMN quality TO status")
            else:
                self._conn.execute("UPDATE document_revisions SET status = quality WHERE status = 'draft' AND quality != 'draft'")
                self._conn.execute("ALTER TABLE document_revisions DROP COLUMN quality")
        if "verified_by" in rev_cols:
            if "approved_by" not in rev_cols:
                self._conn.execute("ALTER TABLE document_revisions RENAME COLUMN verified_by TO approved_by")
            else:
                self._conn.execute("UPDATE document_revisions SET approved_by = verified_by WHERE approved_by = '' AND verified_by != ''")
                self._conn.execute("ALTER TABLE document_revisions DROP COLUMN verified_by")
        if "flagged_by" in rev_cols:
            if "in_review_by" not in rev_cols:
                self._conn.execute("ALTER TABLE document_revisions RENAME COLUMN flagged_by TO in_review_by")
            else:
                self._conn.execute("UPDATE document_revisions SET in_review_by = flagged_by WHERE in_review_by = '' AND flagged_by != ''")
                self._conn.execute("ALTER TABLE document_revisions DROP COLUMN flagged_by")

        # Drop old index name so the new one can be created
        self._conn.execute("DROP INDEX IF EXISTS idx_doc_revisions_quality")

        # Migrate old status values: verified → approved, incorrect → in_review
        self._conn.execute("UPDATE documents SET status = 'approved' WHERE status = 'verified'")
        self._conn.execute("UPDATE documents SET status = 'in_review' WHERE status = 'incorrect'")

        # Populate categories from category for existing rows that haven't been migrated
        self._conn.execute(
            "UPDATE documents SET categories = json_array(category) WHERE categories = '[]' AND category != ''"
        )

        # Ensure author is never empty
        self._conn.execute("UPDATE documents SET author = 'Local' WHERE TRIM(author) = ''")
        self._conn.execute("UPDATE document_revisions SET author = 'Local' WHERE TRIM(author) = ''")

        # Normalize category arrays: remove empty values and ensure primary category is present when non-empty
        self._normalize_category_arrays()

        # Backfill revision snapshots for existing rows
        self._conn.execute(
            """INSERT INTO document_revisions (
                   file_path, revision, category, title, content, date, author,
                   approved_by, in_review_by, status, categories, updated_at
               )
               SELECT d.file_path, d.revision, d.category, d.title, d.content, d.date, d.author,
                      d.approved_by, d.in_review_by, d.status, d.categories, d.updated_at
               FROM documents d
               WHERE NOT EXISTS (
                   SELECT 1 FROM document_revisions r
                   WHERE r.file_path = d.file_path AND r.revision = d.revision
               )"""
        )
        self._conn.commit()

    def _normalize_category_arrays(self) -> None:
        docs = self._conn.execute("SELECT file_path, category, categories FROM documents").fetchall()
        for row in docs:
            category = row["category"] or ""
            raw = row["categories"] or "[]"
            try:
                cats = [c for c in json.loads(raw) if isinstance(c, str) and c.strip()]
            except Exception:
                cats = []
            if category and category not in cats:
                cats = [category] + cats
            self._conn.execute(
                "UPDATE documents SET categories = ? WHERE file_path = ?",
                (json.dumps(cats), row["file_path"]),
            )

        revs = self._conn.execute("SELECT file_path, revision, category, categories FROM document_revisions").fetchall()
        for row in revs:
            category = row["category"] or ""
            raw = row["categories"] or "[]"
            try:
                cats = [c for c in json.loads(raw) if isinstance(c, str) and c.strip()]
            except Exception:
                cats = []
            if category and category not in cats:
                cats = [category] + cats
            self._conn.execute(
                "UPDATE document_revisions SET categories = ? WHERE file_path = ? AND revision = ?",
                (json.dumps(cats), row["file_path"], int(row["revision"])),
            )

    def get_manifest(self) -> dict[str, tuple[str, float]]:
        """Return {file_path: (file_hash, last_modified)} for all indexed docs."""
        rows = self._conn.execute(
            "SELECT file_path, file_hash, last_modified FROM documents"
        ).fetchall()
        return {r["file_path"]: (r["file_hash"], r["last_modified"]) for r in rows}

    def upsert_document(
        self,
        file_path: str,
        category: str | None,
        title: str,
        content: str,
        file_hash: str,
        last_modified: float,
        date: str = "",
        author: str | None = "",
        approved_by: str = "",
        in_review_by: str = "",
        status: str | None = None,
        quality: str = "draft",
        categories: list[str] | None = None,
        revision: int | None = None,
        create_revision: bool = True,
    ) -> None:
        """Insert or update a document in the store."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        category = category or ""
        author = (author or "Local").strip() or "Local"
        status_val = status if status is not None else quality

        # Ensure primary category is always in categories list
        if categories is None:
            categories = [category] if category else []
        elif category and category not in categories:
            categories = [category] + categories
        categories_json = json.dumps(categories)

        existing = self._conn.execute(
            "SELECT id, categories, revision FROM documents WHERE file_path = ?", (file_path,)
        ).fetchone()

        if existing:
            current_revision = int(existing["revision"])
            update_current_row = True
            if revision is None:
                revision_value = (current_revision + 1) if create_revision else current_revision
            else:
                revision_value = int(revision)
                if create_revision and revision_value < current_revision:
                    update_current_row = False

            # Preserve existing extra categories on re-index
            existing_cats = json.loads(existing["categories"]) if existing["categories"] else []
            if category and categories == [category] and existing_cats:
                # Re-indexing from disk — keep manually added categories
                merged = existing_cats
                if category not in merged:
                    merged = [category] + merged
                categories_json = json.dumps(merged)

            if update_current_row:
                self._conn.execute(
                    """UPDATE documents
                       SET category = ?, title = ?, content = ?,
                           file_hash = ?, last_modified = ?,
                           date = ?, author = ?, approved_by = ?,
                           in_review_by = ?, status = ?, revision = ?, updated_at = ?,
                           categories = ?
                       WHERE file_path = ?""",
                    (category, title, content, file_hash, last_modified,
                     date, author, approved_by, in_review_by, status_val, revision_value, now,
                     categories_json, file_path),
                )
        else:
            revision_value = int(revision) if revision is not None else 1
            self._conn.execute(
                """INSERT INTO documents
                   (file_path, category, title, content, file_hash, last_modified,
                    date, author, approved_by, in_review_by, status, revision, updated_at, categories)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (file_path, category, title, content, file_hash, last_modified,
                  date, author, approved_by, in_review_by, status_val, revision_value, now, categories_json),
            )

        if create_revision:
            self._upsert_revision_snapshot(
                file_path=file_path,
                revision=revision_value,
                category=category,
                title=title,
                content=content,
                date=date,
                author=author,
                approved_by=approved_by,
                in_review_by=in_review_by,
                status=status_val,
                categories_json=categories_json,
                updated_at=now,
            )

        self._conn.commit()

    def _upsert_revision_snapshot(
        self,
        file_path: str,
        revision: int,
        category: str,
        title: str,
        content: str,
        date: str,
        author: str | None,
        approved_by: str,
        in_review_by: str,
        status: str,
        categories_json: str,
        updated_at: str,
    ) -> None:
        self._conn.execute(
            """INSERT INTO document_revisions (
                   file_path, revision, category, title, content, date, author,
                   approved_by, in_review_by, status, categories, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(file_path, revision) DO UPDATE SET
                   category = excluded.category,
                   title = excluded.title,
                   content = excluded.content,
                   date = excluded.date,
                   author = excluded.author,
                   approved_by = excluded.approved_by,
                   in_review_by = excluded.in_review_by,
                   status = excluded.status,
                   categories = excluded.categories,
                   updated_at = excluded.updated_at""",
            (
                file_path,
                revision,
                category,
                title,
                content,
                date,
                author or "",
                approved_by or "",
                in_review_by,
                status,
                categories_json,
                updated_at,
            ),
        )

    def delete_document(self, file_path: str) -> None:
        """Remove a document and its revision history from the store."""
        self._conn.execute(
            "DELETE FROM document_revisions WHERE file_path = ?", (file_path,)
        )
        self._conn.execute(
            "DELETE FROM documents WHERE file_path = ?", (file_path,)
        )
        self._conn.commit()

    def delete_category(self, category: str) -> None:
        """Remove all documents in a category, including revision history."""
        rows = self._conn.execute(
            "SELECT file_path FROM documents WHERE category = ?", (category,)
        ).fetchall()
        for row in rows:
            self._conn.execute(
                "DELETE FROM document_revisions WHERE file_path = ?", (row["file_path"],)
            )
        self._conn.execute(
            "DELETE FROM documents WHERE category = ?", (category,)
        )
        self._conn.commit()

    def purge_orphan_revisions(self) -> int:
        """Delete revision rows whose file_path no longer exists in current documents."""
        cur = self._conn.execute(
            """DELETE FROM document_revisions
               WHERE file_path NOT IN (SELECT file_path FROM documents)"""
        )
        self._conn.commit()
        return int(cur.rowcount or 0)

    def search(
        self,
        query: str,
        category: str | None = None,
        limit: int = 10,
        verified_only: bool = False,
        latest_approved: bool = False,
    ) -> list[FTSResult]:
        """Full-text search with BM25 ranking."""
        if not query.strip():
            return []

        if latest_approved:
            return self.search_latest_approved(query, category=category, limit=limit)

        # Escape FTS5 special characters for safety
        safe_query = _fts5_escape(query)

        quality_filter = "AND d.status = 'approved'" if verified_only else ""

        if category:
            # Search in documents where the category is in the categories JSON array
            cat_filter = "AND EXISTS (SELECT 1 FROM json_each(d.categories) WHERE json_each.value = ?)"
            rows = self._conn.execute(
                f"""SELECT d.file_path, d.category, d.title,
                          snippet(documents_fts, 1, '<mark>', '</mark>', '…', 40) AS snippet,
                          rank
                   FROM documents_fts
                   JOIN documents d ON d.id = documents_fts.rowid
                   WHERE documents_fts MATCH ?
                     {cat_filter}
                     {quality_filter}
                   ORDER BY rank
                   LIMIT ?""",
                (safe_query, category, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                f"""SELECT d.file_path, d.category, d.title,
                          snippet(documents_fts, 1, '<mark>', '</mark>', '…', 40) AS snippet,
                          rank
                   FROM documents_fts
                   JOIN documents d ON d.id = documents_fts.rowid
                   WHERE documents_fts MATCH ?
                     {quality_filter}
                   ORDER BY rank
                   LIMIT ?""",
                (safe_query, limit),
            ).fetchall()

        return [
            FTSResult(
                file_path=r["file_path"],
                category=r["category"],
                title=r["title"],
                snippet=r["snippet"],
                rank=r["rank"],
            )
            for r in rows
        ]

    def search_latest_approved(
        self,
        query: str,
        category: str | None = None,
        limit: int = 10,
    ) -> list[FTSResult]:
        """Search only latest approved revisions for each document."""
        if not query.strip():
            return []

        words = [w.lower() for w in query.strip().split() if w.strip()]
        if not words:
            return []

        cat_filter = ""
        params: list = []
        if category:
            cat_filter = "AND EXISTS (SELECT 1 FROM json_each(r.categories) WHERE json_each.value = ?)"
            params.append(category)

        rows = self._conn.execute(
            f"""WITH latest_approved AS (
                    SELECT file_path, MAX(revision) AS revision
                    FROM document_revisions
                    WHERE status = 'approved'
                    GROUP BY file_path
                )
                SELECT r.file_path, r.category, r.title, r.content
                FROM latest_approved la
                JOIN document_revisions r
                  ON r.file_path = la.file_path AND r.revision = la.revision
                WHERE 1=1 {cat_filter}""",
            params,
        ).fetchall()

        scored: list[tuple[int, FTSResult]] = []
        for row in rows:
            title_l = row["title"].lower()
            content_l = row["content"].lower()

            score = 0
            first_idx = -1
            for w in words:
                in_title = w in title_l
                in_content = w in content_l
                if in_title:
                    score += 3
                if in_content:
                    score += 1
                    idx = content_l.find(w)
                    if first_idx == -1 or (idx != -1 and idx < first_idx):
                        first_idx = idx

            if score <= 0:
                continue

            snippet = row["content"][:160]
            if first_idx >= 0:
                start = max(0, first_idx - 40)
                end = min(len(row["content"]), first_idx + 120)
                snippet = row["content"][start:end]

            scored.append(
                (
                    score,
                    FTSResult(
                        file_path=row["file_path"],
                        category=row["category"],
                        title=row["title"],
                        snippet=snippet,
                        rank=float(-score),
                    ),
                )
            )

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in scored[:limit]]

    def get_document(self, file_path: str, latest_approved: bool = False) -> dict | None:
        """Get a single document by file path (current or latest approved revision)."""
        if latest_approved:
            row = self._conn.execute(
                """SELECT file_path, category, title, content, date, author, approved_by,
                          in_review_by, status, updated_at, categories, revision
                   FROM document_revisions
                   WHERE file_path = ? AND status = 'approved'
                   ORDER BY revision DESC
                   LIMIT 1""",
                (file_path,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT file_path, category, title, content, date, author, approved_by, in_review_by, status, revision, updated_at, categories FROM documents WHERE file_path = ?",
                (file_path,),
            ).fetchone()
        if not row:
            return None
        return _normalize_document_dict(dict(row))

    def update_metadata(
        self,
        file_path: str,
        date: str | None = None,
        author: str | None = None,
        approved_by: str | None = None,
        in_review_by: str | None = None,
        status: str | None = None,
        quality: str | None = None,
    ) -> bool:
        """Update metadata fields for a document. Returns True if document exists."""
        existing = self._conn.execute(
            "SELECT id FROM documents WHERE file_path = ?", (file_path,)
        ).fetchone()
        if not existing:
            return False

        updates: list[str] = []
        params: list[str] = []
        if date is not None:
            updates.append("date = ?")
            params.append(date)
        if author is not None:
            updates.append("author = ?")
            params.append(author)
        if approved_by is not None:
            updates.append("approved_by = ?")
            params.append(approved_by)
        if in_review_by is not None:
            updates.append("in_review_by = ?")
            params.append(in_review_by)
        status_value = status if status is not None else quality
        if status_value is not None:
            if status_value not in ("draft", "in_review", "approved"):
                return False
            updates.append("status = ?")
            params.append(status_value)

        if not updates:
            return True

        # Always set updated_at when metadata changes
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        updates.append("updated_at = ?")
        params.append(now)

        params.append(file_path)
        self._conn.execute(
            f"UPDATE documents SET {', '.join(updates)} WHERE file_path = ?",
            params,
        )

        # Keep current revision snapshot metadata in sync (status-only changes do not create a revision)
        current = self._conn.execute(
            "SELECT revision FROM documents WHERE file_path = ?",
            (file_path,),
        ).fetchone()
        if current:
            rev_updates: list[str] = []
            rev_params: list[str] = []
            if date is not None:
                rev_updates.append("date = ?")
                rev_params.append(date)
            if author is not None:
                rev_updates.append("author = ?")
                rev_params.append(author)
            if approved_by is not None:
                rev_updates.append("approved_by = ?")
                rev_params.append(approved_by)
            if in_review_by is not None:
                rev_updates.append("in_review_by = ?")
                rev_params.append(in_review_by)
            if status_value is not None:
                rev_updates.append("status = ?")
                rev_params.append(status_value)
            rev_updates.append("updated_at = ?")
            rev_params.append(now)
            rev_params.extend([file_path, int(current["revision"])])
            self._conn.execute(
                f"UPDATE document_revisions SET {', '.join(rev_updates)} WHERE file_path = ? AND revision = ?",
                rev_params,
            )

        self._conn.commit()
        return True

    def update_categories(self, file_path: str, categories: list[str]) -> bool:
        """Update the categories list for a document. Returns True if document exists."""
        existing = self._conn.execute(
            "SELECT id, category FROM documents WHERE file_path = ?", (file_path,)
        ).fetchone()
        if not existing:
            return False

        # Normalize incoming categories; empty list means uncategorized
        categories = [c.strip() for c in categories if isinstance(c, str) and c.strip()]
        primary = categories[0] if categories else ""

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._conn.execute(
            "UPDATE documents SET category = ?, categories = ?, updated_at = ? WHERE file_path = ?",
            (primary, json.dumps(categories), now, file_path),
        )

        current = self._conn.execute(
            "SELECT revision FROM documents WHERE file_path = ?",
            (file_path,),
        ).fetchone()
        if current:
            self._conn.execute(
                "UPDATE document_revisions SET category = ?, categories = ?, updated_at = ? WHERE file_path = ? AND revision = ?",
                (primary, json.dumps(categories), now, file_path, int(current["revision"])),
            )

        self._conn.commit()
        return True

    def get_all_documents(self) -> list[dict]:
        """Get all documents with full metadata."""
        rows = self._conn.execute(
            "SELECT file_path, category, title, content, date, author, approved_by, in_review_by, status, revision, updated_at, categories FROM documents ORDER BY category, title"
        ).fetchall()
        result = []
        for r in rows:
            d = _normalize_document_dict(dict(r))
            result.append(d)
        return result

    def get_latest_documents(self, category: str | None = None, approved_only: bool = False) -> list[dict]:
        """Get the newest revision per file path, optionally filtered by category and approved state."""
        quality_filter = "AND r.status = 'approved'" if approved_only else ""
        cat_filter = ""
        params: list = []
        if category:
            cat_filter = "AND EXISTS (SELECT 1 FROM json_each(r.categories) WHERE json_each.value = ?)"
            params.append(category)

        rows = self._conn.execute(
            f"""WITH latest AS (
                    SELECT file_path, MAX(revision) AS revision
                    FROM document_revisions
                    GROUP BY file_path
                )
                SELECT r.file_path, r.category, r.title, r.content, r.date, r.author,
                       r.approved_by, r.in_review_by, r.status, r.revision, r.updated_at, r.categories
                FROM latest l
                JOIN document_revisions r
                  ON r.file_path = l.file_path AND r.revision = l.revision
                WHERE 1=1 {quality_filter} {cat_filter}
                ORDER BY r.category, r.title""",
            params,
        ).fetchall()
        result = []
        for r in rows:
            d = _normalize_document_dict(dict(r))
            result.append(d)
        return result

    def get_all_revisions(self, category: str | None = None) -> list[dict]:
        """Get all revisions, optionally by category membership."""
        cat_filter = ""
        params: list = []
        if category:
            cat_filter = "WHERE EXISTS (SELECT 1 FROM json_each(r.categories) WHERE json_each.value = ?)"
            params.append(category)
        rows = self._conn.execute(
            f"""SELECT r.file_path, r.category, r.title, r.content, r.date, r.author,
                       r.approved_by, r.in_review_by, r.status, r.revision, r.updated_at, r.categories
                FROM document_revisions r
                {cat_filter}
                ORDER BY r.file_path, r.revision""",
            params,
        ).fetchall()
        result = []
        for r in rows:
            d = _normalize_document_dict(dict(r))
            result.append(d)
        return result

    def get_latest_approved_documents(self, category: str | None = None) -> list[dict]:
        """Get latest approved revision per document, optionally filtered by category."""
        cat_filter = ""
        params: list = []
        if category:
            cat_filter = "AND EXISTS (SELECT 1 FROM json_each(r.categories) WHERE json_each.value = ?)"
            params.append(category)

        rows = self._conn.execute(
            f"""WITH latest_approved AS (
                    SELECT file_path, MAX(revision) AS revision
                    FROM document_revisions
                    WHERE status = 'approved'
                    GROUP BY file_path
                )
                SELECT r.file_path, r.category, r.title, r.content, r.date, r.author,
                       r.approved_by, r.in_review_by, r.status, r.revision, r.updated_at, r.categories
                FROM latest_approved la
                JOIN document_revisions r
                  ON r.file_path = la.file_path AND r.revision = la.revision
                WHERE 1=1 {cat_filter}
                ORDER BY r.category, r.title""",
            params,
        ).fetchall()
        result = []
        for r in rows:
            d = _normalize_document_dict(dict(r))
            result.append(d)
        return result

    def get_revisions(self, file_path: str) -> list[dict]:
        """Get revision history for a document, newest first."""
        rows = self._conn.execute(
            """SELECT file_path, category, title, date, author, approved_by, in_review_by,
                      status, revision, updated_at, categories
               FROM document_revisions
               WHERE file_path = ?
               ORDER BY revision DESC""",
            (file_path,),
        ).fetchall()
        result = []
        for r in rows:
            d = _normalize_document_dict(dict(r))
            result.append(d)
        return result

    def reconcile_revisions(self, file_path: str, keep_revisions: set[int]) -> None:
        """Keep only specified revisions for a file and realign current document row."""
        keep = sorted({int(r) for r in keep_revisions if int(r) > 0})
        if not keep:
            return

        placeholders = ",".join("?" for _ in keep)
        params: list = [file_path, *keep]
        self._conn.execute(
            f"DELETE FROM document_revisions WHERE file_path = ? AND revision NOT IN ({placeholders})",
            params,
        )

        latest = self._conn.execute(
            """SELECT category, title, content, date, author, approved_by,
                      in_review_by, status, revision, updated_at, categories
               FROM document_revisions
               WHERE file_path = ?
               ORDER BY revision DESC
               LIMIT 1""",
            (file_path,),
        ).fetchone()

        if latest:
            self._conn.execute(
                """UPDATE documents
                   SET category = ?, title = ?, content = ?, date = ?, author = ?,
                       approved_by = ?, in_review_by = ?, status = ?, revision = ?,
                       updated_at = ?, categories = ?
                   WHERE file_path = ?""",
                (
                    latest["category"],
                    latest["title"],
                    latest["content"],
                    latest["date"],
                    latest["author"],
                    latest["approved_by"],
                    latest["in_review_by"],
                    latest["status"],
                    int(latest["revision"]),
                    latest["updated_at"],
                    latest["categories"],
                    file_path,
                ),
            )
        else:
            self._conn.execute("DELETE FROM documents WHERE file_path = ?", (file_path,))

        self._conn.commit()

    def get_revision(self, file_path: str, revision: int) -> dict | None:
        """Get a specific document revision."""
        row = self._conn.execute(
            """SELECT file_path, category, title, content, date, author, approved_by,
                      in_review_by, status, revision, updated_at, categories
               FROM document_revisions
               WHERE file_path = ? AND revision = ?""",
            (file_path, revision),
        ).fetchone()
        if not row:
            return None
        return _normalize_document_dict(dict(row))

    def get_review_queue(self) -> list[dict]:
        """Get documents that are draft or in_review, newest updated_at first."""
        rows = self._conn.execute(
            """SELECT file_path, category, title, date, author, in_review_by, status, updated_at
               FROM documents
               WHERE status IN ('draft', 'in_review')
               ORDER BY
                   CASE WHEN updated_at = '' THEN 1 ELSE 0 END,
                   updated_at DESC,
                   title ASC"""
        ).fetchall()
        return [_normalize_document_dict(dict(r)) for r in rows]

    def get_documents_by_category(self, category: str) -> list[dict]:
        """Get all documents in a category (checks categories JSON array)."""
        rows = self._conn.execute(
            """SELECT file_path, category, title, content, date, author, approved_by, in_review_by, status, updated_at, categories
               FROM documents
               WHERE EXISTS (SELECT 1 FROM json_each(categories) WHERE json_each.value = ?)
               ORDER BY title""",
            (category,),
        ).fetchall()
        return [_normalize_document_dict(dict(r)) for r in rows]

    def get_all_categories(self) -> list[dict]:
        """Get all categories with document counts (from categories JSON arrays)."""
        rows = self._conn.execute(
            """SELECT json_each.value AS category, COUNT(*) AS doc_count
               FROM documents, json_each(documents.categories)
               WHERE TRIM(json_each.value) != ''
               GROUP BY json_each.value
               ORDER BY json_each.value"""
        ).fetchall()
        return [dict(r) for r in rows]

    def get_category_overlaps(self) -> list[dict]:
        """Get overlap counts between category pairs (shared documents).

        Returns list of {"source": cat_a, "target": cat_b, "shared": count}.
        """
        rows = self._conn.execute(
            """SELECT a.value AS cat_a, b.value AS cat_b, COUNT(*) AS shared
               FROM documents d,
                    json_each(d.categories) a,
                    json_each(d.categories) b
                WHERE a.value < b.value
                  AND TRIM(a.value) != ''
                  AND TRIM(b.value) != ''
               GROUP BY a.value, b.value
               ORDER BY shared DESC"""
        ).fetchall()
        return [{"source": r["cat_a"], "target": r["cat_b"], "shared": r["shared"]} for r in rows]

    def get_stats(self) -> dict:
        """Get overall statistics."""
        row = self._conn.execute(
            "SELECT COUNT(*) as total_docs, COUNT(DISTINCT category) as total_categories FROM documents"
        ).fetchone()
        return dict(row)

    def close(self) -> None:
        self._conn.close()


def _fts5_escape(query: str) -> str:
    """Escape a user query for safe FTS5 matching.

    Wraps each word in quotes to prevent FTS5 syntax injection.
    """
    words = query.strip().split()
    if not words:
        return '""'
    return " ".join(f'"{w}"' for w in words)


def _normalize_document_dict(d: dict) -> dict:
    cats_raw = d.get("categories", "[]")
    if isinstance(cats_raw, str):
        cats = json.loads(cats_raw)
    elif isinstance(cats_raw, list):
        cats = cats_raw
    else:
        cats = []
    d["categories"] = [c for c in cats if isinstance(c, str) and c.strip()]
    if d.get("category", "") == "":
        d["category"] = None
    if d.get("author", "") == "":
        d["author"] = "Local"
    status = d.get("status") or d.get("quality") or "draft"
    d["status"] = status
    # Provide backward compat aliases
    d["quality"] = status
    if "approved_by" in d:
        d["verified_by"] = d["approved_by"]
    if "in_review_by" in d:
        d["flagged_by"] = d["in_review_by"]
    return d
