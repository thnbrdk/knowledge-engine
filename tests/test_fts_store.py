"""Tests for the SQLite FTS5 store."""

import json

from rag_mcp.fts_store import FTSStore, _fts5_escape


class TestFTSStore:
    def test_upsert_and_get(self, fts: FTSStore):
        fts.upsert_document(
            file_path="test/doc.md",
            category="test",
            title="Test Doc",
            content="Hello world from test document.",
            file_hash="abc123",
            last_modified=1000.0,
            categories=["test"],
        )
        doc = fts.get_document("test/doc.md")
        assert doc is not None
        assert doc["title"] == "Test Doc"
        assert doc["category"] == "test"
        assert doc["categories"] == ["test"]

    def test_upsert_update(self, fts: FTSStore):
        fts.upsert_document(
            file_path="test/doc.md",
            category="test",
            title="V1",
            content="Version 1",
            file_hash="aaa",
            last_modified=1000.0,
        )
        fts.upsert_document(
            file_path="test/doc.md",
            category="test",
            title="V2",
            content="Version 2",
            file_hash="bbb",
            last_modified=2000.0,
        )
        doc = fts.get_document("test/doc.md")
        assert doc["title"] == "V2"

    def test_search(self, fts: FTSStore):
        fts.upsert_document(
            file_path="a.md", category="cat", title="Alpha",
            content="Python programming language", file_hash="h1", last_modified=1.0,
        )
        fts.upsert_document(
            file_path="b.md", category="cat", title="Beta",
            content="Docker containers and orchestration", file_hash="h2", last_modified=1.0,
        )
        results = fts.search("python")
        assert len(results) >= 1
        assert results[0].title == "Alpha"

    def test_search_with_category_filter(self, fts: FTSStore):
        fts.upsert_document(
            file_path="a.md", category="python", title="Py Doc",
            content="Python basics", file_hash="h1", last_modified=1.0,
            categories=["python"],
        )
        fts.upsert_document(
            file_path="b.md", category="devops", title="Docker Doc",
            content="Python in Docker containers", file_hash="h2", last_modified=1.0,
            categories=["devops"],
        )
        results = fts.search("python", category="python")
        assert len(results) == 1
        assert results[0].title == "Py Doc"

    def test_search_empty_query(self, fts: FTSStore):
        assert fts.search("") == []
        assert fts.search("   ") == []

    def test_delete_document(self, fts: FTSStore):
        fts.upsert_document(
            file_path="del.md", category="test", title="To Delete",
            content="Deletable", file_hash="h", last_modified=1.0,
        )
        fts.upsert_document(
            file_path="del.md", category="test", title="To Delete 2",
            content="Deletable 2", file_hash="h2", last_modified=2.0,
            create_revision=True,
        )
        assert fts.get_document("del.md") is not None
        fts.delete_document("del.md")
        assert fts.get_document("del.md") is None
        assert fts.get_revisions("del.md") == []

    def test_delete_category(self, fts: FTSStore):
        for i in range(3):
            fts.upsert_document(
                file_path=f"cat/{i}.md", category="killme", title=f"Doc {i}",
                content=f"Content {i}", file_hash=f"h{i}", last_modified=1.0,
            )
        fts.delete_category("killme")
        assert fts.get_document("cat/0.md") is None
        assert fts.get_revisions("cat/0.md") == []

    def test_purge_orphan_revisions(self, fts: FTSStore):
        fts.upsert_document(
            file_path="orphan.md", category="test", title="Orphan",
            content="Orphan", file_hash="h", last_modified=1.0,
        )
        # Simulate legacy/orphan state
        fts._conn.execute("DELETE FROM documents WHERE file_path = ?", ("orphan.md",))
        fts._conn.commit()

        removed = fts.purge_orphan_revisions()
        assert removed >= 1
        assert fts.get_revisions("orphan.md") == []

    def test_get_all_categories(self, fts: FTSStore):
        fts.upsert_document(
            file_path="a.md", category="python", title="A",
            content="A", file_hash="h1", last_modified=1.0,
            categories=["python", "engineering"],
        )
        fts.upsert_document(
            file_path="b.md", category="devops", title="B",
            content="B", file_hash="h2", last_modified=1.0,
            categories=["devops"],
        )
        cats = fts.get_all_categories()
        cat_names = {c["category"] for c in cats}
        assert "python" in cat_names
        assert "devops" in cat_names
        assert "engineering" in cat_names

    def test_get_category_overlaps(self, fts: FTSStore):
        fts.upsert_document(
            file_path="a.md", category="python", title="A",
            content="A", file_hash="h1", last_modified=1.0,
            categories=["python", "engineering"],
        )
        overlaps = fts.get_category_overlaps()
        assert len(overlaps) == 1
        assert overlaps[0]["shared"] == 1

    def test_update_categories(self, fts: FTSStore):
        fts.upsert_document(
            file_path="a.md", category="python", title="A",
            content="A", file_hash="h1", last_modified=1.0,
            categories=["python"],
        )
        fts.update_categories("a.md", ["python", "devops"])
        doc = fts.get_document("a.md")
        assert "devops" in doc["categories"]

    def test_update_categories_promotes_uncategorized_primary(self, fts: FTSStore):
        fts.upsert_document(
            file_path="u.md", category="", title="U",
            content="U", file_hash="h1", last_modified=1.0,
            categories=[],
        )
        fts.update_categories("u.md", ["security"])
        doc = fts.get_document("u.md")
        assert doc["category"] == "security"
        assert doc["categories"] == ["security"]

    def test_update_categories_can_clear_to_uncategorized(self, fts: FTSStore):
        fts.upsert_document(
            file_path="c.md", category="python", title="C",
            content="C", file_hash="h1", last_modified=1.0,
            categories=["python", "devops"],
        )
        fts.update_categories("c.md", [])
        doc = fts.get_document("c.md")
        assert doc["category"] is None
        assert doc["categories"] == []

    def test_update_metadata(self, fts: FTSStore):
        fts.upsert_document(
            file_path="a.md", category="test", title="A",
            content="A", file_hash="h", last_modified=1.0,
        )
        ok = fts.update_metadata("a.md", status="approved", approved_by="XY")
        assert ok is True
        doc = fts.get_document("a.md")
        assert doc["status"] == "approved"
        assert doc["approved_by"] == "XY"

    def test_update_metadata_nonexistent(self, fts: FTSStore):
        ok = fts.update_metadata("nope.md", status="approved")
        assert ok is False

    def test_get_manifest(self, fts: FTSStore):
        fts.upsert_document(
            file_path="m.md", category="test", title="M",
            content="M", file_hash="hashval", last_modified=42.0,
        )
        manifest = fts.get_manifest()
        assert "m.md" in manifest
        assert manifest["m.md"] == ("hashval", 42.0)

    def test_get_stats(self, fts: FTSStore):
        fts.upsert_document(
            file_path="s.md", category="a", title="S",
            content="S", file_hash="h", last_modified=1.0,
        )
        stats = fts.get_stats()
        assert stats["total_docs"] == 1
        assert stats["total_categories"] == 1

    def test_get_review_queue(self, fts: FTSStore):
        fts.upsert_document(
            file_path="draft.md", category="test", title="Draft",
            content="D", file_hash="h1", last_modified=1.0, status="draft",
        )
        fts.upsert_document(
            file_path="approved.md", category="test", title="Approved",
            content="A", file_hash="h2", last_modified=1.0, status="approved",
        )
        queue = fts.get_review_queue()
        titles = [q["title"] for q in queue]
        assert "Draft" in titles
        assert "Approved" not in titles

    def test_get_documents_by_category_multi(self, fts: FTSStore):
        fts.upsert_document(
            file_path="multi.md", category="python", title="Multi",
            content="Multi cat doc", file_hash="h", last_modified=1.0,
            categories=["python", "engineering"],
        )
        py_docs = fts.get_documents_by_category("python")
        eng_docs = fts.get_documents_by_category("engineering")
        assert any(d["file_path"] == "multi.md" for d in py_docs)
        assert any(d["file_path"] == "multi.md" for d in eng_docs)

    def test_latest_approved_doc_and_search(self, fts: FTSStore):
        fts.upsert_document(
            file_path="rev.md", category="python", title="Doc",
            content="Approved baseline", file_hash="h1", last_modified=1.0,
            status="approved",
        )
        fts.upsert_document(
            file_path="rev.md", category="python", title="Doc",
            content="Draft newer content", file_hash="h2", last_modified=2.0,
            status="draft",
        )

        approved = fts.get_document("rev.md", latest_approved=True)
        assert approved is not None
        assert approved["status"] == "approved"
        assert "baseline" in approved["content"]

        results = fts.search("baseline", latest_approved=True)
        assert any(r.file_path == "rev.md" for r in results)


class TestFTS5Escape:
    def test_simple_words(self):
        assert _fts5_escape("hello world") == '"hello" "world"'

    def test_empty(self):
        assert _fts5_escape("") == '""'
        assert _fts5_escape("   ") == '""'

    def test_single_word(self):
        assert _fts5_escape("python") == '"python"'
