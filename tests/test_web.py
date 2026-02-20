"""Tests for the Starlette web application and REST API."""

import io
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient

from rag_mcp.config import WebConfig
from rag_mcp.web.app import create_web_app


@pytest.fixture()
def app(config, fts, vectors, indexer, indexed):
    """Starlette app with populated index."""
    return create_web_app(config, fts, vectors, indexer)


@pytest.fixture()
async def client(app):
    """Async HTTP test client for the web app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture()
def app_with_token(config, fts, vectors, indexer, indexed):
    """Starlette app with admin token enabled."""
    config.web = WebConfig(
        enabled=config.web.enabled,
        host=config.web.host,
        port=config.web.port,
        admin_token="secret-token",
    )
    return create_web_app(config, fts, vectors, indexer)


@pytest.fixture()
async def client_with_token(app_with_token):
    """Async HTTP test client for auth-required web app."""
    transport = ASGITransport(app=app_with_token)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
class TestPublicPages:
    async def test_index_page(self, client: AsyncClient):
        r = await client.get("/")
        assert r.status_code == 200
        assert "knowledge" in r.text.lower() or "categories" in r.text.lower()

    async def test_category_page(self, client: AsyncClient):
        r = await client.get("/category/python")
        assert r.status_code == 200
        assert "python" in r.text.lower()

    async def test_search_page(self, client: AsyncClient):
        r = await client.get("/search?q=python")
        assert r.status_code == 200

    async def test_search_page_no_query(self, client: AsyncClient):
        r = await client.get("/search")
        assert r.status_code == 200

    async def test_admin_page(self, client: AsyncClient):
        r = await client.get("/admin")
        assert r.status_code == 200


@pytest.mark.asyncio
class TestRestAPI:
    async def test_api_categories(self, client: AsyncClient):
        r = await client.get("/api/categories")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        cat_names = {c["category"] for c in data}
        assert "python" in cat_names

    async def test_api_category_documents(self, client: AsyncClient):
        r = await client.get("/api/categories/python")
        assert r.status_code == 200
        docs = r.json()
        assert isinstance(docs, list)
        assert len(docs) >= 1

    async def test_api_search_keyword(self, client: AsyncClient):
        r = await client.get("/api/search?q=python&type=keyword")
        assert r.status_code == 200
        results = r.json()
        assert len(results) >= 1

    async def test_api_search_semantic(self, client: AsyncClient):
        r = await client.get("/api/search?q=programming+language&type=semantic")
        assert r.status_code == 200
        results = r.json()
        assert len(results) >= 1

    async def test_api_category_graph(self, client: AsyncClient):
        r = await client.get("/api/category-graph")
        assert r.status_code == 200
        data = r.json()
        assert "categories" in data
        assert "overlaps" in data

    async def test_api_document_not_found(self, client: AsyncClient):
        r = await client.get("/api/documents/nonexistent.md")
        assert r.status_code == 404


@pytest.mark.asyncio
class TestDocumentAPI:
    async def test_update_metadata(self, client: AsyncClient, fts):
        docs = fts.get_all_documents()
        fp = docs[0]["file_path"]
        r = await client.patch(
            f"/api/documents/{fp}/metadata",
            json={"status": "approved", "approved_by": "TS"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "updated"

    async def test_update_metadata_invalid_status(self, client: AsyncClient, fts):
        docs = fts.get_all_documents()
        fp = docs[0]["file_path"]
        r = await client.patch(
            f"/api/documents/{fp}/metadata",
            json={"status": "bogus"},
        )
        assert r.status_code == 400

    async def test_update_categories(self, client: AsyncClient, fts):
        docs = fts.get_all_documents()
        fp = docs[0]["file_path"]
        r = await client.patch(
            f"/api/documents/{fp}/metadata",
            json={"categories": ["python", "security"]},
        )
        assert r.status_code == 200
        doc = fts.get_document(fp)
        assert "security" in doc["categories"]

    async def test_update_content(self, client: AsyncClient, fts):
        docs = fts.get_all_documents()
        approved = next(d for d in docs if d["status"] == "approved")
        fp = approved["file_path"]
        old_revision = approved.get("revision", 1)
        r = await client.put(
            f"/api/documents/{fp}/content",
            json={"content": "# Updated\n\nNew content here."},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "updated"
        assert r.json()["document_status"] == "draft"
        assert r.json()["revision"] == old_revision + 1

    async def test_revisions_and_compare(self, client: AsyncClient, fts):
        docs = fts.get_all_documents()
        approved_doc = next(d for d in docs if d["status"] == "approved")
        fp = approved_doc["file_path"]
        base_rev = approved_doc.get("revision", 1)

        r1 = await client.get(f"/api/documents/{fp}/revisions")
        assert r1.status_code == 200
        initial = r1.json()
        assert len(initial) >= 1

        first = await client.put(
            f"/api/documents/{fp}/content",
            json={"content": "first revision body"},
        )
        second = await client.put(
            f"/api/documents/{fp}/content",
            json={"content": "second revision body"},
        )
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["revision"] == base_rev + 1
        assert second.json()["revision"] == base_rev + 1

        r2 = await client.get(f"/api/documents/{fp}/revisions")
        assert r2.status_code == 200
        revs = r2.json()
        assert len(revs) >= 2

        left = revs[0]["revision"]
        right = revs[1]["revision"]
        cmp_resp = await client.get(
            f"/api/documents/compare?file_path={fp}&left={left}&right={right}"
        )
        assert cmp_resp.status_code == 200
        payload = cmp_resp.json()
        assert payload["left"]["revision"] == left
        assert payload["right"]["revision"] == right
        assert "html" in payload["left"]
        assert "html" in payload["right"]
        assert "diff_html" in payload

    async def test_export_modes(self, client: AsyncClient, fts):
        docs = fts.get_all_documents()
        approved_doc = next(d for d in docs if d["status"] == "approved")
        fp = approved_doc["file_path"]
        bump = await client.put(
            f"/api/documents/{fp}/content",
            json={"content": "new revision for export test"},
        )
        assert bump.status_code == 200

        r_all = await client.get("/api/admin/export?mode=all")
        assert r_all.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r_all.content), "r") as zf:
            names = zf.namelist()
            assert any(name.endswith(".md") for name in names)
            assert any(name.endswith(".mdx") for name in names)
            sample = zf.read(names[0]).decode("utf-8")
            assert "revision:" in sample

        r_newest = await client.get("/api/admin/export?mode=newest")
        assert r_newest.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r_newest.content), "r") as zf:
            names = zf.namelist()
            assert all(".rev" not in name for name in names)

        r_approved = await client.get("/api/admin/export?mode=newest_approved")
        assert r_approved.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r_approved.content), "r") as zf:
            names = zf.namelist()
            assert len(names) >= 1
            for name in names:
                content = zf.read(name).decode("utf-8")
                assert 'status: "approved"' in content

    async def test_update_content_not_found(self, client: AsyncClient):
        r = await client.put(
            "/api/documents/nonexistent.md/content",
            json={"content": "nope"},
        )
        assert r.status_code == 404

    async def test_update_metadata_requires_auth(self, client_with_token: AsyncClient, fts):
        docs = fts.get_all_documents()
        fp = docs[0]["file_path"]

        unauthorized = await client_with_token.patch(
            f"/api/documents/{fp}/metadata",
            json={"status": "approved", "approved_by": "TS"},
        )
        assert unauthorized.status_code == 401

        authorized = await client_with_token.patch(
            f"/api/documents/{fp}/metadata",
            headers={"Authorization": "Bearer secret-token"},
            json={"status": "approved", "approved_by": "TS"},
        )
        assert authorized.status_code == 200

    async def test_update_content_requires_auth(self, client_with_token: AsyncClient, fts):
        docs = fts.get_all_documents()
        fp = docs[0]["file_path"]

        unauthorized = await client_with_token.put(
            f"/api/documents/{fp}/content",
            json={"content": "# Updated\n\nNew content here."},
        )
        assert unauthorized.status_code == 401

        authorized = await client_with_token.put(
            f"/api/documents/{fp}/content",
            headers={"Authorization": "Bearer secret-token"},
            json={"content": "# Updated\n\nNew content here."},
        )
        assert authorized.status_code == 200

    async def test_update_metadata_categories_missing_doc_returns_404(self, client: AsyncClient):
        r = await client.patch(
            "/api/documents/nonexistent.md/metadata",
            json={"categories": ["python", "security"]},
        )
        assert r.status_code == 404
