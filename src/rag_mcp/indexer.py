"""Indexing pipeline: crawl, parse, diff, and upsert into stores."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from .config import Config
from .crawler import discover_files
from .fts_store import FTSStore
from .markdown_parser import parse_markdown
from .vector_store import VectorStore

logger = logging.getLogger(__name__)


class Indexer:
    def __init__(
        self,
        config: Config,
        fts: FTSStore,
        vectors: VectorStore,
    ) -> None:
        self.config = config
        self.fts = fts
        self.vectors = vectors

    def run_full_sync(self, force: bool = False) -> dict:
        """Run indexing across all .md files. Returns summary stats.

        With force=False (startup default): only index new files and clean up
        deleted files. Existing files are left untouched.
        With force=True (manual reindex): also re-check existing files for changes
        using mtime+hash comparison.
        """
        files = discover_files(self.config.knowledge_dir)
        manifest = self.fts.get_manifest()
        recovery_revisions: dict[str, set[int]] = {}
        archive_targets: set[str] = set()

        stats = {"new": 0, "updated": 0, "deleted": 0, "unchanged": 0, "skipped": 0}

        all_disk_files: set[str] = set()

        for file_path in files:
            fp_str = self._logical_file_key(file_path)
            all_disk_files.add(fp_str)

            rev_hint = self._extract_revision_hint(file_path)
            if rev_hint is not None:
                recovery_revisions.setdefault(fp_str, set()).add(rev_hint)
            if file_path.suffix.lower() == ".mdx":
                archive_targets.add(fp_str)

            if fp_str in manifest:
                if not force:
                    # Startup mode: skip files already in the DB
                    stats["unchanged"] += 1
                    continue

                stored_hash, stored_mtime = manifest[fp_str]
                current_mtime = file_path.stat().st_mtime

                if current_mtime == stored_mtime:
                    stats["unchanged"] += 1
                    continue

                current_hash = _hash_file(file_path)
                if current_hash == stored_hash:
                    self.fts._conn.execute(
                        "UPDATE documents SET last_modified = ? WHERE file_path = ?",
                        (current_mtime, fp_str),
                    )
                    self.fts._conn.commit()
                    stats["unchanged"] += 1
                    continue

                # Content changed → re-index
                if self._index_file(file_path):
                    stats["updated"] += 1
                else:
                    stats["skipped"] += 1
            else:
                # New file
                if self._index_file(file_path):
                    stats["new"] += 1
                else:
                    stats["skipped"] += 1

        # Find deleted files
        for fp_str in manifest:
            if fp_str not in all_disk_files:
                self.vectors.delete_by_file_all(fp_str)
                self.fts.delete_document(fp_str)
                stats["deleted"] += 1

        # Always keep vectors clean: only approved current documents are embedded
        for doc in self.fts.get_all_documents():
            if doc.get("status", "draft") != "approved":
                self.vectors.delete_by_file_all(doc["file_path"])

        # Remove any historical rows that no longer have a current document
        self.fts.purge_orphan_revisions()

        # For archive-based recovery, keep only revisions that are present on disk
        for file_key in archive_targets:
            keep = recovery_revisions.get(file_key, set())
            if keep:
                self.fts.reconcile_revisions(file_key, keep)

        logger.info(
            "Index sync: %d new, %d updated, %d deleted, %d unchanged, %d skipped (missing categories)",
            stats["new"],
            stats["updated"],
            stats["deleted"],
            stats["unchanged"],
            stats["skipped"],
        )

        # Compact vector store to clean up old versions
        if stats["new"] or stats["updated"] or stats["deleted"]:
            self.vectors.compact()

        return stats

    def reindex_category(self, category: str) -> dict:
        """Purge and re-index a single category.

        Finds all files whose frontmatter categories include this category,
        deletes the old index entries, and re-indexes them.
        """
        self.fts.delete_category(category)
        self.vectors.delete_category(category)

        files = discover_files(self.config.knowledge_dir)
        indexed = 0
        for file_path in files:
            parsed = parse_markdown(file_path)
            if category in parsed.meta.categories:
                self._index_file(file_path)
                indexed += 1

        stats = {"indexed": indexed, "category": category}
        logger.info("Re-indexed category '%s': %d files", category, indexed)
        return stats

    def add_category(self, category_path: str) -> dict:
        """Create a category folder and index it."""
        full_path = self.config.knowledge_dir / category_path
        full_path.mkdir(parents=True, exist_ok=True)

        # Index any .md files that might already be there with this category
        files = discover_files(self.config.knowledge_dir)
        indexed = 0
        for f in files:
            parsed = parse_markdown(f)
            if category_path in parsed.meta.categories:
                self._index_file(f)
                indexed += 1

        return {"category": category_path, "indexed": indexed}

    def delete_category_index(self, category: str, delete_files: bool = False) -> dict:
        """Remove a category from the index. Optionally delete files from disk."""
        self.fts.delete_category(category)
        self.vectors.delete_category(category)

        deleted_files = 0
        if delete_files:
            cat_path = self.config.knowledge_dir / category
            if cat_path.is_dir():
                import shutil
                shutil.rmtree(cat_path)
                deleted_files = 1  # directory removed

        return {"category": category, "files_deleted": delete_files, "removed": True}

    def add_document(
        self,
        title: str,
        content: str,
        category: str,
        author: str = "",
        categories: list[str] | None = None,
    ) -> dict:
        """Create a new markdown file on disk and index it.

        Returns info about the created document.
        """
        import re
        from datetime import date as _date

        # MCP-created documents default to AI-Agent author
        author = "AI-Agent"

        # Use provided categories list, or fall back to just the primary
        all_categories = categories if categories else [category]

        # Sanitize title into a filename
        slug = re.sub(r"[^\w\s-]", "", title.lower())
        slug = re.sub(r"[\s]+", "-", slug).strip("-")
        if not slug:
            slug = "untitled"

        cat_dir = self.config.knowledge_dir / category
        cat_dir.mkdir(parents=True, exist_ok=True)

        file_path = cat_dir / f"{slug}.md"
        # Avoid overwriting
        counter = 1
        while file_path.exists():
            file_path = cat_dir / f"{slug}-{counter}.md"
            counter += 1

        # Build minimal frontmatter with all categories
        categories_yaml = "[" + ", ".join(all_categories) + "]"
        frontmatter_lines = [
            "---",
            f"title: {title}",
            f"author: {author}",
            f"categories: {categories_yaml}",
            "---",
            "",
        ]
        full_content = "\n".join(frontmatter_lines) + content

        file_path.write_text(full_content, encoding="utf-8")

        # Index the new file
        self._index_file(file_path)
        logical_file_path = self._logical_file_key(file_path)

        return {
            "file_path": logical_file_path,
            "category": category,
            "title": title,
            "status": "draft",
            "author": author,
            "date": _date.today().isoformat(),
        }

    def update_document(
        self,
        file_path: str,
        content: str,
        author: str = "",
    ) -> dict:
        """Update an existing document's content on disk and re-index it.

        Rewrites the markdown file preserving/updating frontmatter, sets status
        back to 'draft', and re-indexes. Returns info about the updated document.
        """
        doc = self.fts.get_document(file_path)
        if not doc:
            return {"error": f"Document not found: {file_path}"}

        should_create_revision = doc.get("status", doc.get("quality")) == "approved"

        path = self._resolve_disk_path(file_path)
        if not path.exists():
            return {"error": f"File not found on disk: {file_path}"}

        title = doc["title"]
        category = doc["category"]
        categories = doc.get("categories", [category])
        categories_yaml = "[" + ", ".join(categories) + "]"

        # Build minimal frontmatter; revision/status are tracked in DB and added on export
        frontmatter_lines = [
            "---",
            f"title: {title}",
            f"categories: {categories_yaml}",
            "---",
            "",
        ]
        full_content = "\n".join(frontmatter_lines) + content

        path.write_text(full_content, encoding="utf-8")

        # Re-index file with forced revision increment and draft reset
        parsed = parse_markdown(path)
        file_hash = _hash_file(path)
        mtime = path.stat().st_mtime

        self.fts.upsert_document(
            file_path=doc["file_path"],
            category=category,
            title=title,
            content=parsed.content,
            file_hash=file_hash,
            last_modified=mtime,
            date=doc.get("date", ""),
            author=author or doc.get("author", ""),
            approved_by="",
            in_review_by="",
            status="draft",
            categories=categories,
            revision=None,
            create_revision=should_create_revision,
        )

        self.sync_vector_index(doc["file_path"])

        return {
            "file_path": doc["file_path"],
            "category": category,
            "title": title,
            "status": "draft",
            "author": author or doc.get("author", ""),
            "date": doc.get("date", ""),
        }

    def update_frontmatter_categories(self, file_path: str, categories: list[str]) -> bool:
        """Update the categories field in the file's YAML frontmatter on disk.

        Returns True if the file was updated, False if file not found.
        """
        import frontmatter as fm

        path = Path(file_path)
        if not path.is_absolute():
            path = self.config.knowledge_dir / path
        if not path.exists():
            path = Path(file_path)
        if not path.exists():
            return False

        post = fm.load(path)
        post.metadata["categories"] = categories
        path.write_text(fm.dumps(post), encoding="utf-8")
        return True

    def find_best_category(self, title: str, content: str) -> str | None:
        """Try to find the best existing category for the given content.

        Uses semantic search to find the most similar existing documents, then returns
        their category. Returns None if no good match is found.
        """
        query = f"{title} {content[:500]}"
        results = self.vectors.search(query, n_results=3)

        if not results:
            return None

        # If the top result is reasonably close, use its category
        if results[0].distance < 1.5:
            return results[0].category

        return None

    def _index_file(self, file_path: Path) -> bool:
        """Parse and index a single file into both stores.

        Documents without categories/frontmatter are still indexed using defaults.
        """
        parsed = parse_markdown(file_path)
        logical_path, forced_revision = self._normalize_exported_revision_path(parsed.meta.file_path)
        parsed.meta.file_path = logical_path
        if forced_revision is not None:
            parsed.meta.revision = forced_revision

        file_hash = _hash_file(file_path)
        mtime = file_path.stat().st_mtime

        categories = parsed.meta.categories
        primary_category = categories[0] if categories else ""

        existing = self.fts.get_document(parsed.meta.file_path)
        frontmatter = parsed.meta.frontmatter

        has_revision = "revision" in frontmatter or forced_revision is not None
        revision_value = parsed.meta.revision if has_revision else None
        should_create_revision = (
            existing is None
            or has_revision
            or existing.get("status", existing.get("quality")) == "approved"
        )

        date_value = parsed.meta.date if "date" in frontmatter else (existing.get("date", parsed.meta.date) if existing else parsed.meta.date)
        author_value = parsed.meta.author if "author" in frontmatter else (existing.get("author", None) if existing else None)
        has_approved_by = "approved_by" in frontmatter or "verified_by" in frontmatter
        has_in_review_by = "in_review_by" in frontmatter or "flagged_by" in frontmatter
        approved_value = parsed.meta.approved_by if has_approved_by else (existing.get("approved_by", existing.get("verified_by", "")) if existing else "")
        in_review_value = parsed.meta.in_review_by if has_in_review_by else (existing.get("in_review_by", existing.get("flagged_by", "")) if existing else "")
        has_status = "status" in frontmatter or "quality" in frontmatter
        status_value = parsed.meta.status if has_status else (existing.get("status", existing.get("quality", "draft")) if existing else "draft")

        # Upsert into FTS
        self.fts.upsert_document(
            file_path=parsed.meta.file_path,
            category=primary_category,
            title=parsed.meta.title,
            content=parsed.content,
            file_hash=file_hash,
            last_modified=mtime,
            date=date_value,
            author=author_value,
            approved_by=approved_value,
            in_review_by=in_review_value,
            status=status_value,
            categories=categories,
            revision=revision_value,
            create_revision=should_create_revision,
        )

        self.sync_vector_index(parsed.meta.file_path)
        return True

    def sync_vector_index(self, file_path: str) -> bool:
        """Ensure vector embeddings exist only for currently approved document state."""
        doc = self.fts.get_document(file_path)
        if not doc:
            return False

        self.vectors.delete_by_file_all(file_path)

        if doc.get("status", "draft") != "approved":
            return True

        path = self._resolve_disk_path(file_path)
        if not path.exists():
            return False

        parsed = parse_markdown(path)
        chunk_category = doc.get("category") or "_uncategorized"
        chunk_dicts = []
        for c in parsed.chunks:
            suffix = c.chunk_id.split("::", 1)[1] if "::" in c.chunk_id else "_chunk"
            chunk_dicts.append({
                "chunk_id": f"{file_path}::{suffix}",
                "content": c.content,
                "file_path": file_path,
                "category": chunk_category,
                "title": doc.get("title", c.title),
                "heading_path": c.heading_path,
            })
        self.vectors.upsert_chunks(chunk_category, chunk_dicts)
        return True

    def _resolve_disk_path(self, file_path: str) -> Path:
        path = Path(file_path)
        if not path.is_absolute():
            path = self.config.knowledge_dir / path
        if path.exists():
            return path

        # Resolve by filename anywhere under knowledge_dir (flat DB IDs)
        filename = Path(file_path).name
        matches = sorted(self.config.knowledge_dir.rglob(filename))
        if matches:
            md_matches = [m for m in matches if m.suffix.lower() == ".md"]
            return md_matches[0] if md_matches else matches[0]

        return Path(file_path)

    def _logical_file_key(self, file_path: Path) -> str:
        logical, _ = self._normalize_exported_revision_path(file_path.as_posix())
        return logical

    def _normalize_exported_revision_path(self, file_path: str) -> tuple[str, int | None]:
        name = Path(file_path).name
        match = re.match(r"^(.*)\.rev(\d+)\.mdx$", name)
        if not match:
            return name, None
        base = match.group(1)
        revision = int(match.group(2))
        return f"{base}.md", revision

    def _extract_revision_hint(self, file_path: Path) -> int | None:
        logical, forced_revision = self._normalize_exported_revision_path(file_path.as_posix())
        if forced_revision is not None:
            return forced_revision
        if file_path.suffix.lower() != ".md":
            return None
        try:
            parsed = parse_markdown(file_path)
            if "revision" in parsed.meta.frontmatter:
                return int(parsed.meta.revision)
        except Exception:
            return None
        return None


def _hash_file(file_path: Path) -> str:
    """Compute blake2b hash of file contents."""
    h = hashlib.blake2b(digest_size=16)
    h.update(file_path.read_bytes())
    return h.hexdigest()
