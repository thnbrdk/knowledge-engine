"""Starlette web application — public browser + admin panel + REST API."""

from __future__ import annotations

import io
import json
import os
import zipfile
from difflib import SequenceMatcher
from html import escape
from pathlib import Path

import markdown
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from ..config import Config
from ..fts_store import FTSStore
from ..indexer import Indexer
from ..vector_store import VectorStore

_WEB_DIR = Path(__file__).parent
_TEMPLATES_DIR = _WEB_DIR / "templates"
_STATIC_DIR = _WEB_DIR / "static"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
_md = markdown.Markdown(extensions=["fenced_code", "tables", "toc", "codehilite"])


def _is_safe_path(base: Path, target: str) -> bool:
    """Prevent path traversal attacks."""
    try:
        resolved = (base / target).resolve()
        return str(resolved).startswith(str(base.resolve()))
    except (ValueError, OSError):
        return False


# ── Shared state (set by create_web_app) ────────────────────────────────────

_config: Config | None = None
_fts: FTSStore | None = None
_vectors: VectorStore | None = None
_indexer: Indexer | None = None


def create_web_app(
    config: Config,
    fts: FTSStore,
    vectors: VectorStore,
    indexer: Indexer,
) -> Starlette:
    """Create the Starlette web app with all routes."""
    global _config, _fts, _vectors, _indexer
    _config = config
    _fts = fts
    _vectors = vectors
    _indexer = indexer

    routes = [
        # Public pages
        Route("/", endpoint=page_index),
        Route("/category/{path:path}", endpoint=page_category),
        Route("/document/{path:path}", endpoint=page_document),
        Route("/search", endpoint=page_search),
        # Admin page
        Route("/admin", endpoint=page_admin),
        # REST API — public
        Route("/api/categories", endpoint=api_categories),
        Route("/api/categories/{path:path}", endpoint=api_category_documents),
        Route("/api/documents/compare", endpoint=api_document_compare),
        Route("/api/documents/{path:path}/revisions", endpoint=api_document_revisions),
        Route("/api/documents/{path:path}/revisions/{revision:int}", endpoint=api_document_revision),
        Route("/api/documents/{path:path}/compare", endpoint=api_document_compare),
        Route("/api/documents/{path:path}", endpoint=api_document),
        Route("/api/search", endpoint=api_search),
        # REST API — category graph
        Route("/api/category-graph", endpoint=api_category_graph),
        # REST API — admin
        Route("/api/admin/stats", endpoint=api_admin_stats),
        Route("/api/admin/reindex-all", endpoint=api_admin_reindex_all, methods=["POST"]),
        Route("/api/admin/reindex/{path:path}", endpoint=api_admin_reindex, methods=["POST"]),
        Route("/api/admin/category/{path:path}", endpoint=api_admin_delete_category, methods=["DELETE"]),
        Route("/api/admin/export", endpoint=api_admin_export),
        # REST API — document metadata
        Route("/api/documents/{path:path}/metadata", endpoint=api_document_metadata, methods=["PATCH"]),
        Route("/api/documents/{path:path}/content", endpoint=api_document_content, methods=["PUT"]),
        # Static files
        Mount("/static", app=StaticFiles(directory=str(_STATIC_DIR)), name="static"),
    ]

    app = Starlette(routes=routes)
    return app


# ── Auth helper ─────────────────────────────────────────────────────────────


def _check_admin_auth(request: Request) -> bool:
    """Check admin authentication. Returns True if authorized."""
    if not _config or not _config.web.admin_token:
        return True  # No token configured — open access
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:] == _config.web.admin_token
    # Also check cookie
    return request.cookies.get("admin_token") == _config.web.admin_token


# ── Public pages ────────────────────────────────────────────────────────────


async def page_index(request: Request) -> Response:
    cats = _fts.get_all_categories()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "categories": cats,
    })


async def page_category(request: Request) -> Response:
    category = request.path_params["path"]
    if not _is_safe_path(_config.knowledge_dir, category):
        return HTMLResponse("Invalid category path.", status_code=400)
    docs = _fts.get_documents_by_category(category)
    # Build breadcrumbs
    parts = category.split("/")
    breadcrumbs = []
    for i, part in enumerate(parts):
        breadcrumbs.append({
            "name": part,
            "path": "/".join(parts[: i + 1]),
        })
    # Check for subcategories
    all_cats = _fts.get_all_categories()
    subcats = [c for c in all_cats if c["category"].startswith(category + "/") and c["category"] != category]

    return templates.TemplateResponse("category.html", {
        "request": request,
        "category": category,
        "documents": docs,
        "breadcrumbs": breadcrumbs,
        "subcategories": subcats,
    })


async def page_document(request: Request) -> Response:
    file_path = request.path_params["path"]
    doc = _fts.get_document(file_path)
    if not doc:
        return HTMLResponse("Document not found.", status_code=404)

    revisions = _fts.get_revisions(file_path)
    latest_approved = _fts.get_document(file_path, latest_approved=True)

    _md.reset()
    rendered_html = _md.convert(doc["content"])

    # Get related documents
    related = _vectors.search_similar(file_path, doc.get("category") or "_uncategorized", n_results=5)

    return templates.TemplateResponse("document.html", {
        "request": request,
        "doc": doc,
        "rendered_content": rendered_html,
        "related": related,
        "revisions": revisions,
        "latest_approved": latest_approved,
    })


async def page_search(request: Request) -> Response:
    query = request.query_params.get("q", "")
    category = request.query_params.get("category", None)
    results = []
    if query:
        fts_results = _fts.search(query, category=category, limit=20, latest_approved=True)
        results = [
            {"file_path": r.file_path, "category": r.category, "title": r.title, "snippet": r.snippet}
            for r in fts_results
        ]
    cats = _fts.get_all_categories()
    return templates.TemplateResponse("search.html", {
        "request": request,
        "query": query,
        "selected_category": category,
        "results": results,
        "categories": cats,
    })


async def page_admin(request: Request) -> Response:
    cats = _fts.get_all_categories()
    stats = _fts.get_stats()
    review_queue = _fts.get_review_queue()
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "categories": cats,
        "stats": stats,
        "review_queue": review_queue,
        "admin_token_required": bool(_config.web.admin_token),
    })


# ── REST API — public ──────────────────────────────────────────────────────


async def api_categories(request: Request) -> Response:
    cats = _fts.get_all_categories()
    return JSONResponse(cats)


async def api_category_documents(request: Request) -> Response:
    category = request.path_params["path"]
    docs = _fts.get_documents_by_category(category)
    return JSONResponse(docs)


async def api_document(request: Request) -> Response:
    file_path = request.path_params["path"]
    doc = _fts.get_document(file_path)
    if not doc:
        return JSONResponse({"error": "Not found"}, status_code=404)
    _md.reset()
    doc["rendered_html"] = _md.convert(doc["content"])
    doc["revisions"] = _fts.get_revisions(file_path)
    approved = _fts.get_document(file_path, latest_approved=True)
    doc["latest_approved_revision"] = approved.get("revision") if approved else None
    return JSONResponse(doc)


async def api_document_revisions(request: Request) -> Response:
    file_path = request.path_params["path"]
    current = _fts.get_document(file_path)
    if not current:
        return JSONResponse({"error": "Not found"}, status_code=404)
    revisions = _fts.get_revisions(file_path)
    return JSONResponse(revisions)


async def api_document_revision(request: Request) -> Response:
    file_path = request.path_params["path"]
    revision = int(request.path_params["revision"])
    rev = _fts.get_revision(file_path, revision)
    if not rev:
        return JSONResponse({"error": "Not found"}, status_code=404)
    _md.reset()
    rev["rendered_html"] = _md.convert(rev["content"])
    return JSONResponse(rev)


async def api_document_compare(request: Request) -> Response:
    file_path = request.path_params.get("path") or request.query_params.get("file_path")
    if not file_path:
        return JSONResponse({"error": "file_path is required"}, status_code=400)
    left = request.query_params.get("left")
    right = request.query_params.get("right")
    if left is None or right is None:
        return JSONResponse({"error": "left and right revision numbers are required"}, status_code=400)
    if left == "" or right == "":
        return JSONResponse({"error": "left and right must be non-empty revision numbers"}, status_code=400)

    left_rev = _fts.get_revision(file_path, int(left))
    right_rev = _fts.get_revision(file_path, int(right))
    if not left_rev or not right_rev:
        return JSONResponse({"error": "Revision not found"}, status_code=404)

    _md.reset()
    left_html = _md.convert(left_rev["content"])
    _md.reset()
    right_html = _md.convert(right_rev["content"])
    left_lines = left_rev["content"].splitlines()
    right_lines = right_rev["content"].splitlines()
    matcher = SequenceMatcher(a=left_lines, b=right_lines)

    left_render: list[str] = []
    right_render: list[str] = []

    def line_html(line: str, css: str) -> str:
        return f"<div class='diff-line {css}'>{escape(line)}</div>"

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for i in range(i2 - i1):
                left_render.append(line_html(left_lines[i1 + i], "diff-line--same"))
                right_render.append(line_html(right_lines[j1 + i], "diff-line--same"))
        elif tag == "delete":
            for i in range(i1, i2):
                left_render.append(line_html(left_lines[i], "diff-line--del"))
                right_render.append(line_html("", "diff-line--empty"))
        elif tag == "insert":
            for j in range(j1, j2):
                left_render.append(line_html("", "diff-line--empty"))
                right_render.append(line_html(right_lines[j], "diff-line--add"))
        else:  # replace
            span = max(i2 - i1, j2 - j1)
            for offset in range(span):
                left_val = left_lines[i1 + offset] if i1 + offset < i2 else ""
                right_val = right_lines[j1 + offset] if j1 + offset < j2 else ""
                left_css = "diff-line--del" if left_val else "diff-line--empty"
                right_css = "diff-line--add" if right_val else "diff-line--empty"
                left_render.append(line_html(left_val, left_css))
                right_render.append(line_html(right_val, right_css))

    left_diff_html = "".join(left_render)
    right_diff_html = "".join(right_render)
    return JSONResponse({
        "left": {"revision": left_rev["revision"], "title": left_rev["title"], "html": left_html},
        "right": {"revision": right_rev["revision"], "title": right_rev["title"], "html": right_html},
        "left_diff_html": left_diff_html,
        "right_diff_html": right_diff_html,
        "diff_html": "",
    })


async def api_search(request: Request) -> Response:
    query = request.query_params.get("q", "")
    category = request.query_params.get("category", None)
    search_type = request.query_params.get("type", "keyword")
    results = []

    if query:
        if search_type in ("keyword", "hybrid"):
            fts_results = _fts.search(query, category=category, limit=20, latest_approved=True)
            for r in fts_results:
                results.append({
                    "file_path": r.file_path,
                    "category": r.category,
                    "title": r.title,
                    "snippet": r.snippet,
                    "source": "keyword",
                })
        if search_type in ("semantic", "hybrid"):
            vec_results = _vectors.search(query, category=category, n_results=20)
            for r in vec_results:
                results.append({
                    "file_path": r.file_path,
                    "category": r.category,
                    "title": r.title,
                    "snippet": r.content[:200],
                    "source": "semantic",
                })

    return JSONResponse(results)


async def api_category_graph(request: Request) -> Response:
    """Return category nodes (with sizes) and overlaps (shared doc counts) for the bubble diagram."""
    cats = _fts.get_all_categories()
    overlaps = _fts.get_category_overlaps()
    return JSONResponse({"categories": cats, "overlaps": overlaps})


# ── REST API — admin ───────────────────────────────────────────────────────


async def api_admin_stats(request: Request) -> Response:
    if not _check_admin_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    stats = _fts.get_stats()
    cats = _fts.get_all_categories()

    # Calculate data sizes
    sqlite_size = 0
    if _config.sqlite_path.exists():
        sqlite_size = _config.sqlite_path.stat().st_size

    lance_size = 0
    if _config.lance_path.exists():
        for f in _config.lance_path.rglob("*"):
            if f.is_file():
                lance_size += f.stat().st_size

    stats["categories"] = cats
    stats["sqlite_size_mb"] = round(sqlite_size / (1024 * 1024), 2)
    stats["vector_size_mb"] = round(lance_size / (1024 * 1024), 2)
    return JSONResponse(stats)


async def api_admin_reindex_all(request: Request) -> Response:
    if not _check_admin_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    result = _indexer.run_full_sync(force=True)
    # Refresh categories
    _indexer.config = _config
    return JSONResponse(result)


async def api_admin_reindex(request: Request) -> Response:
    if not _check_admin_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    category = request.path_params["path"]
    if not _is_safe_path(_config.knowledge_dir, category):
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    result = _indexer.reindex_category(category)
    return JSONResponse(result)


async def api_admin_delete_category(request: Request) -> Response:
    if not _check_admin_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    category = request.path_params["path"]
    if not _is_safe_path(_config.knowledge_dir, category):
        return JSONResponse({"error": "Invalid path"}, status_code=400)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    delete_files = body.get("delete_files", False)

    result = _indexer.delete_category_index(category, delete_files=delete_files)
    return JSONResponse(result)


async def api_admin_export(request: Request) -> Response:
    if not _check_admin_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    # Optional category filter
    category = request.query_params.get("category", None)
    mode = request.query_params.get("mode", "all")

    if mode not in ("all", "newest", "newest_approved"):
        return JSONResponse({"error": "mode must be one of: all, newest, newest_approved"}, status_code=400)

    if mode == "all":
        docs = _fts.get_all_revisions(category=category)
    elif mode == "newest":
        docs = _fts.get_latest_documents(category=category)
    else:
        docs = _fts.get_latest_approved_documents(category=category)

    if category:
        filename = f"knowledge-export-{mode}-{category}.zip"
    else:
        filename = f"knowledge-export-{mode}.zip"

    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        latest_by_path: dict[str, int] = {}
        if mode == "all":
            for d in docs:
                fp = d["file_path"]
                rev = int(d.get("revision", 1))
                latest_by_path[fp] = max(latest_by_path.get(fp, 0), rev)

        for doc in docs:
            cats = doc.get("categories", [doc["category"]])
            cats_yaml = "[" + ", ".join(cats) + "]"
            # Build export frontmatter (always include metadata + revision)
            fm_lines = [
                "---",
                f"title: \"{doc['title']}\"",
                f"date: \"{doc.get('date', '')}\"",
                f"author: \"{doc.get('author', '')}\"",
                f"categories: {cats_yaml}",
                f"approved_by: \"{doc.get('approved_by', '')}\"",
                f"in_review_by: \"{doc.get('in_review_by', '')}\"",
                f"status: \"{doc.get('status', doc.get('quality', 'draft'))}\"",
                f"revision: {int(doc.get('revision', 1))}",
                "---",
                "",
            ]
            full_content = "\n".join(fm_lines) + doc["content"]
            # Always export in flat structure (filename-only)
            rel_path = Path(doc["file_path"]).name
            if mode == "all":
                base = rel_path[:-3] if rel_path.endswith(".md") else rel_path
                revision_num = int(doc.get("revision", 1))
                latest_revision = latest_by_path.get(doc["file_path"], revision_num)
                if revision_num == latest_revision:
                    rel_path = f"{base}.md"
                else:
                    rel_path = f"{base}.rev{revision_num}.mdx"
            zf.writestr(rel_path, full_content)

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


async def api_document_metadata(request: Request) -> Response:
    if not _check_admin_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    file_path = request.path_params["path"]
    body = await request.json()

    existing_doc = _fts.get_document(file_path)
    if not existing_doc:
        return JSONResponse({"error": "Document not found"}, status_code=404)

    allowed = {
        "date",
        "author",
        "approved_by",
        "in_review_by",
        "status",
        "categories",
    }
    updates = {k: v for k, v in body.items() if k in allowed}

    if not updates:
        return JSONResponse({"error": "No valid fields provided"}, status_code=400)

    if "status" in updates and updates["status"] not in ("draft", "in_review", "approved"):
        return JSONResponse({"error": "status must be draft, in_review, or approved"}, status_code=400)

    # Handle categories separately
    if "categories" in updates:
        cats = updates.pop("categories")
        if not isinstance(cats, list) or not all(isinstance(c, str) for c in cats):
            return JSONResponse({"error": "categories must be a list of strings"}, status_code=400)
        ok = _fts.update_categories(file_path, cats)
        if not ok:
            return JSONResponse({"error": "Document not found"}, status_code=404)
        wrote = _indexer.update_frontmatter_categories(file_path, cats)
        if not wrote:
            return JSONResponse({"error": "Document file not found on disk"}, status_code=404)

    if updates:
        ok = _fts.update_metadata(file_path, **updates)
        if not ok:
            return JSONResponse({"error": "Document not found"}, status_code=404)

    _indexer.sync_vector_index(file_path)

    return JSONResponse({"status": "updated", "file_path": file_path})


async def api_document_content(request: Request) -> Response:
    if not _check_admin_auth(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    file_path = request.path_params["path"]
    body = await request.json()
    content = body.get("content")
    if content is None:
        return JSONResponse({"error": "content field is required"}, status_code=400)

    doc = _fts.get_document(file_path)
    if not doc:
        return JSONResponse({"error": "Document not found"}, status_code=404)

    result = _indexer.update_document(
        file_path=file_path,
        content=content,
        author=doc.get("author", ""),
    )
    if "error" in result:
        return JSONResponse({"error": result["error"]}, status_code=404)

    current = _fts.get_document(file_path)
    return JSONResponse({
        "status": "updated",
        "file_path": file_path,
        "revision": current.get("revision") if current else None,
        "document_status": current.get("status") if current else None,
    })
