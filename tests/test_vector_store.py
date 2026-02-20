"""Tests for the LanceDB vector store."""

from rag_mcp.vector_store import VectorStore


class TestVectorStore:
    def test_upsert_and_search(self, vectors: VectorStore):
        chunks = [
            {
                "chunk_id": "test::intro",
                "content": "Python is a programming language for data science",
                "file_path": "python/basics.md",
                "category": "python",
                "title": "Python Basics",
                "heading_path": [],
            },
            {
                "chunk_id": "test::docker",
                "content": "Docker containers package applications with dependencies",
                "file_path": "devops/docker.md",
                "category": "devops",
                "title": "Docker Guide",
                "heading_path": ["Docker"],
            },
        ]
        vectors.upsert_chunks("python", chunks[:1])
        vectors.upsert_chunks("devops", chunks[1:])

        results = vectors.search("programming language")
        assert len(results) >= 1
        assert results[0].file_path == "python/basics.md"

    def test_search_empty_query(self, vectors: VectorStore):
        assert vectors.search("") == []
        assert vectors.search("   ") == []

    def test_search_with_category_filter(self, vectors: VectorStore):
        vectors.upsert_chunks("python", [
            {
                "chunk_id": "py::1",
                "content": "Python web frameworks like Flask and Django",
                "file_path": "python/web.md",
                "category": "python",
                "title": "Web",
                "heading_path": [],
            },
        ])
        vectors.upsert_chunks("devops", [
            {
                "chunk_id": "ops::1",
                "content": "Kubernetes orchestrates containerized applications",
                "file_path": "devops/k8s.md",
                "category": "devops",
                "title": "K8s",
                "heading_path": [],
            },
        ])
        results = vectors.search("web frameworks", category="python")
        assert all(r.category == "python" for r in results)

    def test_delete_by_file(self, vectors: VectorStore):
        vectors.upsert_chunks("test", [
            {
                "chunk_id": "del::1",
                "content": "Content to delete",
                "file_path": "test/del.md",
                "category": "test",
                "title": "Delete Me",
                "heading_path": [],
            },
        ])
        vectors.delete_by_file("test", "test/del.md")
        results = vectors.search("content to delete", category="test")
        matching = [r for r in results if r.file_path == "test/del.md"]
        assert len(matching) == 0

    def test_upsert_overwrites(self, vectors: VectorStore):
        chunk = {
            "chunk_id": "up::1",
            "content": "Version one content",
            "file_path": "test/up.md",
            "category": "test",
            "title": "Upserted",
            "heading_path": [],
        }
        vectors.upsert_chunks("test", [chunk])
        chunk["content"] = "Version two content updated"
        vectors.upsert_chunks("test", [chunk])
        results = vectors.search("version two updated", category="test")
        assert len(results) >= 1

    def test_compact(self, vectors: VectorStore):
        vectors.upsert_chunks("test", [
            {
                "chunk_id": "c::1",
                "content": "Compaction test content",
                "file_path": "test/compact.md",
                "category": "test",
                "title": "Compact",
                "heading_path": [],
            },
        ])
        stats = vectors.compact()
        assert isinstance(stats, dict)
