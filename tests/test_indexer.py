"""Tests for the indexer pipeline."""

import textwrap
from pathlib import Path

from rag_mcp.fts_store import FTSStore
from rag_mcp.indexer import Indexer
from rag_mcp.vector_store import VectorStore


class TestIndexer:
    def test_full_sync_indexes_files(self, indexer: Indexer, indexed: dict):
        assert indexed["new"] == 4  # basics, advanced, docker, no-cats
        assert indexed["skipped"] == 0

    def test_incremental_sync_unchanged(self, indexer: Indexer, indexed: dict):
        stats2 = indexer.run_full_sync()
        assert stats2["new"] == 0
        assert stats2["unchanged"] == 4

    def test_force_sync_detects_changes(self, indexer: Indexer, indexed: dict, tmp_knowledge: Path):
        # Modify a file
        basics = tmp_knowledge / "python" / "basics.md"
        original = basics.read_text(encoding="utf-8")
        basics.write_text(original + "\n## New Section\n\nAdded content.\n", encoding="utf-8")

        stats = indexer.run_full_sync(force=True)
        assert stats["updated"] >= 1

    def test_deleted_file_detected(self, indexer: Indexer, indexed: dict, tmp_knowledge: Path):
        # Delete a file
        deleted_fp = (tmp_knowledge / "devops" / "docker.md")
        deleted_name = deleted_fp.name
        deleted_fp.unlink()
        stats = indexer.run_full_sync()
        assert stats["deleted"] == 1
        assert indexer.fts.get_document(deleted_name) is None
        assert indexer.fts.get_revisions(deleted_name) == []

    def test_fts_populated_after_index(self, fts: FTSStore, indexed: dict):
        results = fts.search("python")
        assert len(results) >= 1

    def test_vectors_populated_after_index(self, vectors: VectorStore, indexed: dict):
        results = vectors.search("programming language")
        assert len(results) >= 1

    def test_multi_category_indexed(self, fts: FTSStore, indexed: dict):
        # advanced.md has categories: [python, engineering]
        py_docs = fts.get_documents_by_category("python")
        eng_docs = fts.get_documents_by_category("engineering")
        py_paths = {d["file_path"] for d in py_docs}
        eng_paths = {d["file_path"] for d in eng_docs}
        # advanced.md should appear in both
        advanced = [p for p in py_paths if "advanced" in p]
        assert len(advanced) == 1
        assert advanced[0] in eng_paths

    def test_add_document(self, indexer: Indexer, fts: FTSStore, indexed: dict):
        result = indexer.add_document(
            title="New Knowledge",
            content="This is brand new knowledge about testing.",
            category="python",
            author="tester",
            categories=["python", "engineering"],
        )
        assert result["title"] == "New Knowledge"
        assert result["category"] == "python"

        doc = fts.get_document(result["file_path"])
        assert doc is not None
        assert "engineering" in doc["categories"]

    def test_update_document(self, indexer: Indexer, fts: FTSStore, indexed: dict):
        # Update an approved doc -> should create a new revision
        docs = fts.get_all_documents()
        target = next(d for d in docs if d["status"] == "approved")
        old_revision = target.get("revision", 1)
        result = indexer.update_document(
            file_path=target["file_path"],
            content="Updated content here.",
            author="updater",
        )
        assert result.get("error") is None
        assert result["status"] == "draft"  # reset on update
        updated = fts.get_document(target["file_path"])
        assert updated["revision"] == old_revision + 1

    def test_update_document_draft_no_new_revision(self, indexer: Indexer, fts: FTSStore, indexed: dict):
        # Update a non-approved doc -> should NOT create a new revision
        docs = fts.get_all_documents()
        target = next(d for d in docs if d["status"] in ("draft", "in_review"))
        old_revision = target.get("revision", 1)
        result = indexer.update_document(
            file_path=target["file_path"],
            content="Updated draft content here.",
            author="updater",
        )
        assert result.get("error") is None
        updated = fts.get_document(target["file_path"])
        assert updated["revision"] == old_revision
        assert updated["status"] == "draft"

    def test_update_frontmatter_categories(
        self, indexer: Indexer, fts: FTSStore, indexed: dict, tmp_knowledge: Path,
    ):
        basics_path = (tmp_knowledge / "python" / "basics.md").as_posix()
        ok = indexer.update_frontmatter_categories(basics_path, ["python", "devops"])
        assert ok is True

        import frontmatter
        post = frontmatter.load(tmp_knowledge / "python" / "basics.md")
        assert post.metadata["categories"] == ["python", "devops"]

    def test_reindex_category(self, indexer: Indexer, indexed: dict):
        result = indexer.reindex_category("python")
        assert result["indexed"] >= 2  # basics + advanced

    def test_categories_after_index(self, fts: FTSStore, indexed: dict):
        cats = fts.get_all_categories()
        cat_names = {c["category"] for c in cats}
        assert "python" in cat_names
        assert "devops" in cat_names
        assert "engineering" in cat_names  # from advanced.md

    def test_reindex_restores_revision_archives(self, indexer: Indexer, fts: FTSStore, tmp_knowledge: Path):
        latest = tmp_knowledge / "python" / "restore.md"
        latest.write_text(
            """---
title: Restore Me
categories: [python]
status: approved
revision: 4
---

latest body
""",
            encoding="utf-8",
        )

        archived = tmp_knowledge / "python" / "restore.rev1.mdx"
        archived.write_text(
            """---
title: Restore Me
categories: [python]
status: approved
revision: 1
---

older body
""",
            encoding="utf-8",
        )

        archived2 = tmp_knowledge / "python" / "restore.rev2.mdx"
        archived2.write_text(
            """---
title: Restore Me
categories: [python]
status: approved
revision: 2
---

middle body
""",
            encoding="utf-8",
        )

        archived3 = tmp_knowledge / "python" / "restore.rev3.mdx"
        archived3.write_text(
            """---
title: Restore Me
categories: [python]
status: approved
revision: 3
---

newer archived body
""",
            encoding="utf-8",
        )

        indexer.run_full_sync(force=True)

        archived2.unlink()
        indexer.run_full_sync(force=True)

        fp = latest.name
        current = fts.get_document(fp)
        assert current is not None
        assert current["revision"] == 4

        revisions = fts.get_revisions(fp)
        assert [r["revision"] for r in revisions] == [4, 3, 1]
