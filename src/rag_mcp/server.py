"""FastMCP server with tools, resources, and prompts for RAG."""

from __future__ import annotations

import logging
import sys
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts import base

from .config import Config, load_config
from .fts_store import FTSStore
from .indexer import Indexer
from .vector_store import VectorStore
from .web.app import create_web_app

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    config: Config
    fts: FTSStore
    vectors: VectorStore
    indexer: Indexer


@asynccontextmanager
async def app_lifespan(server: FastMCP):
    """Initialize stores, run indexer, register dynamic tools."""
    # Determine config path
    config_path = Path("rag-config.yaml")
    config = load_config(config_path)

    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.knowledge_dir.mkdir(parents=True, exist_ok=True)

    fts = FTSStore(config.sqlite_path)
    vectors = VectorStore(config.lance_path)
    indexer = Indexer(config, fts, vectors)

    # Run incremental indexing
    logger.info("Starting index sync...")
    stats = indexer.run_full_sync()
    logger.info("Index sync complete: %s", stats)

    ctx = AppContext(
        config=config, fts=fts, vectors=vectors, indexer=indexer
    )

    # Register per-category search tools
    # (removed — use 'search' with category parameter instead)

    # Start web server in background thread if enabled
    web_server: uvicorn.Server | None = None
    if config.web.enabled:
        web_app = create_web_app(config, fts, vectors, indexer)
        web_config = uvicorn.Config(
            web_app,
            host=config.web.host,
            port=config.web.port,
            log_level="warning",
        )
        web_server = uvicorn.Server(web_config)
        web_thread = threading.Thread(target=web_server.run, daemon=True)
        web_thread.start()
        logger.info("Web UI available at http://%s:%d", config.web.host, config.web.port)

    try:
        yield ctx
    finally:
        if web_server:
            web_server.should_exit = True
        fts.close()
        vectors.close()


mcp = FastMCP(
    "RAG Knowledge Server",
    instructions=(
        "You have access to a personal knowledge base containing curated documents "
        "on various technical topics. ALWAYS search the knowledge base before answering "
        "questions about programming, engineering, or technology topics.\n\n"
        "Workflow for answering questions:\n"
        "1. Use `search` with the user's question to find relevant documents (returns titles, file paths, and snippets).\n"
        "2. Use `get_document(file_path)` to read the full content of the most relevant result.\n"
        "3. Synthesize your answer using knowledge base content as the primary source.\n"
        "4. Cite the source documents by title when using information from the knowledge base.\n\n"
        "Use `list_categories` or `browse_category` to explore what knowledge is available.\n"
        "Use `get_related` after reading a document to discover additional relevant knowledge.\n\n"
        "Tool grouping guidance for clients/agents:\n"
        "- Read/exploration tools: search, get_document, get_related, list_categories, browse_category\n"
        "- Write/management tools: save_knowledge, update_knowledge\n"
        "Some clients expose write tools behind a separate activation step. "
        "If a write call reports 'tool is disabled', activate knowledge management/write tools first, then retry.\n\n"
        "When the user shares new knowledge or useful information during conversation, "
        "offer to save it using `save_knowledge` for future reference.\n\n"
        "Only latest approved revisions should be used as authoritative sources. "
        "Draft documents may contain unverified information."
    ),
    lifespan=app_lifespan,
)


# ── Tools ───────────────────────────────────────────────────────────────────


@mcp.tool()
async def search(
    query: str,
    category: str | None = None,
    search_type: str = "hybrid",
) -> str:
    """Search the personal knowledge base for relevant documents. Use this FIRST when the user asks about any technical topic to check for existing knowledge. search_type: 'keyword' (exact match), 'semantic' (meaning-based), or 'hybrid' (both, recommended). Returns titles, file paths, and snippets — use get_document to read the full content of interesting results."""
    ctx: AppContext = mcp.get_context().request_context.lifespan_context
    return _do_search(ctx, query, category, search_type)


@mcp.tool()
async def get_document(file_path: str) -> str:
    """Retrieve the full content of a specific document by file path. Use after search to read the most relevant result."""
    ctx: AppContext = mcp.get_context().request_context.lifespan_context
    doc = ctx.fts.get_document(file_path, latest_approved=True)
    if not doc:
        return f"No approved revision found for document: {file_path}"
    return (
        f"# {doc['title']}\n"
        f"**Category:** {doc['category']}\n"
        f"**Path:** {doc['file_path']}\n"
        f"**Revision:** {doc.get('revision', 1)}\n\n"
        f"{doc['content']}"
    )


@mcp.tool()
async def get_related(file_path: str, n: int = 5) -> str:
    """Find documents related to a given document by semantic similarity. Use to discover additional relevant knowledge after reading a document."""
    ctx: AppContext = mcp.get_context().request_context.lifespan_context
    doc = ctx.fts.get_document(file_path, latest_approved=True)
    if not doc:
        return f"No approved revision found for document: {file_path}"

    results = ctx.vectors.search_similar(file_path, doc.get("category") or "_uncategorized", n_results=n)
    if not results:
        return "No related documents found."

    lines: list[str] = [f"Documents related to '{doc['title']}':"]
    for r in results:
        lines.append(f"- **{r.title}** [{r.category}] — {r.file_path} (similarity: {1 - r.distance:.2f})")
    return "\n".join(lines)


@mcp.tool()
async def list_categories() -> str:
    """List all knowledge categories with document counts. Use to understand what topics are covered in the knowledge base."""
    ctx: AppContext = mcp.get_context().request_context.lifespan_context
    cats = ctx.fts.get_all_categories()
    if not cats:
        return "No categories found. Add markdown files to the knowledge directory."
    lines: list[str] = ["Knowledge categories:"]
    total = 0
    for c in cats:
        lines.append(f"- **{c['category']}** — {c['doc_count']} documents")
        total += c["doc_count"]
    lines.append(f"\nTotal: {total} documents in {len(cats)} categories")
    return "\n".join(lines)


@mcp.tool()
async def browse_category(category: str) -> str:
    """Browse all documents in a category. Use when the user wants to explore a topic area or when you need to find specific documents within a domain."""
    ctx: AppContext = mcp.get_context().request_context.lifespan_context
    docs = ctx.fts.get_documents_by_category(category)
    if not docs:
        return f"No documents found in category '{category}'. Use list_categories to see available categories."
    lines: list[str] = [f"Documents in '{category}' ({len(docs)} total):"]
    for d in docs:
        status = d.get("status", "draft")
        author = d.get("author", "")
        author_str = f" by {author}" if author else ""
        lines.append(f"- **{d['title']}** [{status}]{author_str}\n  {d['file_path']}")
    return "\n".join(lines)


@mcp.tool()
async def save_knowledge(
    title: str,
    content: str,
    author: str,
    categories: list[str] | None = None,
) -> str:
    """Save a new knowledge document from the current conversation.

    Note for tool-enabled clients: this is a write/management operation.
    If your client reports this tool as disabled, activate knowledge
    management/write tools first and retry.

    Creates a markdown file with metadata (date, author, status=draft) and indexes it.
    If categories are not provided, automatically guesses the best category from existing
    categories using semantic search. Always provide categories as a list (e.g. ["python", "devops"]).
    """
    ctx: AppContext = mcp.get_context().request_context.lifespan_context

    if not categories:
        # Always try to guess the best category
        best = ctx.indexer.find_best_category(title, content)
        if best:
            categories = [best]
        else:
            cats = ctx.fts.get_all_categories()
            cat_list = ", ".join(c["category"] for c in cats) if cats else "(none)"
            return (
                f"Could not auto-detect a suitable category. "
                f"Existing categories: {cat_list}. "
                f"Please call save_knowledge again with an explicit 'categories' parameter."
            )

    primary = categories[0]

    result = ctx.indexer.add_document(
        title=title,
        content=content,
        category=primary,
        author=author,
        categories=categories,
    )

    return (
        f"Knowledge saved successfully!\n"
        f"- **Title:** {result['title']}\n"
        f"- **Categories:** {', '.join(categories)}\n"
        f"- **Author:** {result['author']}\n"
        f"- **Date:** {result['date']}\n"
        f"- **Status:** {result['status']}\n"
        f"- **File:** {result['file_path']}"
    )


@mcp.tool()
async def update_knowledge(
    file_path: str,
    content: str,
    author: str = "",
) -> str:
    """Update an existing knowledge document with new or improved content.

    Note for tool-enabled clients: this is a write/management operation.
    If your client reports this tool as disabled, activate knowledge
    management/write tools first and retry.

    IMPORTANT: This replaces the ENTIRE document body. Before calling this tool:
    1. Use get_document(file_path) to read the current content
    2. Merge the existing content with the new information
    3. Pass the complete merged content as the 'content' parameter

    The tool preserves title and categories, resets status to 'draft',
    updates the timestamp, and re-indexes. Use get_document or search to find
    the file_path first.
    """
    ctx: AppContext = mcp.get_context().request_context.lifespan_context

    result = ctx.indexer.update_document(
        file_path=file_path,
        content=content,
        author=author,
    )

    if "error" in result:
        return result["error"]

    return (
        f"Knowledge updated successfully!\n"
        f"- **Title:** {result['title']}\n"
        f"- **Category:** {result['category']}\n"
        f"- **Author:** {result['author']}\n"
        f"- **Date:** {result['date']}\n"
        f"- **Status:** {result['status']} (reset to draft)\n"
        f"- **File:** {result['file_path']}"
    )


# ── Resources ───────────────────────────────────────────────────────────────


@mcp.resource("rag://categories")
async def resource_categories() -> str:
    """List of all knowledge categories."""
    ctx: AppContext = mcp.get_context().request_context.lifespan_context
    cats = ctx.fts.get_all_categories()
    lines = [f"{c['category']} ({c['doc_count']} docs)" for c in cats]
    return "\n".join(lines) if lines else "No categories."


@mcp.resource("rag://{category}/documents")
async def resource_category_documents(category: str) -> str:
    """List documents in a category."""
    ctx: AppContext = mcp.get_context().request_context.lifespan_context
    docs = ctx.fts.get_documents_by_category(category)
    if not docs:
        return f"No documents in category '{category}'."
    lines = [f"- {d['title']} ({d['file_path']})" for d in docs]
    return "\n".join(lines)


# ── Prompts ─────────────────────────────────────────────────────────────────


@mcp.prompt()
def ask(question: str) -> list[base.Message]:
    """Answer a question using the knowledge base. Searches first, then synthesizes."""
    return [
        base.UserMessage(
            f"Use the 'search' tool to find information about: {question}\n"
            "Then use 'get_document' to get full details.\n"
            "Synthesize a comprehensive answer based on the knowledge base content."
        ),
    ]


@mcp.prompt()
def summarize(topic: str) -> list[base.Message]:
    """Summarize everything known about a topic across all categories."""
    return [
        base.UserMessage(
            f"Use the 'search' tool to find all information about: {topic}\n"
            "Search across all categories. Use 'get_document' to retrieve full content.\n"
            "Provide a comprehensive summary of everything in the knowledge base about this topic."
        ),
    ]


# ── Hybrid search logic ────────────────────────────────────────────────────


def _do_search(
    ctx: AppContext,
    query: str,
    category: str | None = None,
    search_type: str = "hybrid",
    verified_only: bool = True,
) -> str:
    """Execute search and format results. By default only returns latest approved revisions."""
    results: list[dict] = []

    if search_type in ("keyword", "hybrid"):
        fts_results = ctx.fts.search(
            query,
            category=category,
            limit=10,
            verified_only=verified_only,
            latest_approved=verified_only,
        )
        for i, r in enumerate(fts_results):
            results.append({
                "file_path": r.file_path,
                "category": r.category,
                "title": r.title,
                "snippet": r.snippet,
                "source": "keyword",
                "rank": i + 1,
            })

    if search_type in ("semantic", "hybrid"):
        vec_results = ctx.vectors.search(query, category=category, n_results=10)
        # Post-filter vector results for latest approved revisions if needed
        if verified_only:
            filtered = []
            for r in vec_results:
                approved = ctx.fts.get_document(r.file_path, latest_approved=True)
                if approved:
                    filtered.append((r, approved))
            vec_results = [item[0] for item in filtered]
            approved_map = {item[0].file_path: item[1] for item in filtered}
        else:
            approved_map = {}
        for i, r in enumerate(vec_results):
            approved = approved_map.get(r.file_path)
            results.append({
                "file_path": r.file_path,
                "category": approved["category"] if approved else r.category,
                "title": approved["title"] if approved else r.title,
                "snippet": (
                    approved["content"][:200] + "…"
                    if approved and len(approved["content"]) > 200
                    else (approved["content"] if approved else (r.content[:200] + "…" if len(r.content) > 200 else r.content))
                ),
                "source": "semantic",
                "rank": i + 1,
            })

    if search_type == "hybrid" and results:
        results = _reciprocal_rank_fusion(results)

    if not results:
        return f"No results found for '{query}'."

    lines: list[str] = [f"Search results for '{query}':"]
    for i, r in enumerate(results[:10], 1):
        lines.append(
            f"\n{i}. **{r['title']}** [{r['category']}]\n"
            f"   Source: {r['file_path']}\n"
            f"   {r['snippet']}"
        )
    return "\n".join(lines)


def _reciprocal_rank_fusion(results: list[dict], k: int = 60) -> list[dict]:
    """Merge keyword and semantic results using RRF."""
    scores: dict[str, float] = {}
    best: dict[str, dict] = {}

    for r in results:
        fp = r["file_path"]
        rrf_score = 1.0 / (k + r["rank"])
        scores[fp] = scores.get(fp, 0.0) + rrf_score
        if fp not in best:
            best[fp] = r.copy()

    # Sort by combined RRF score descending
    ranked_fps = sorted(scores, key=lambda fp: scores[fp], reverse=True)
    return [best[fp] for fp in ranked_fps]


# ── Entry point ─────────────────────────────────────────────────────────────


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    mcp.run()


if __name__ == "__main__":
    main()
