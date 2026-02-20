# Plan: Universal RAG MCP Server

A Python-based MCP server (FastMCP) that ingests markdown files from any folder structure, uses YAML frontmatter `categories` to organize documents (supporting multiple categories per file), indexes into SQLite FTS5 (keyword search) and LanceDB (semantic search via fastembed), and exposes global search tools/resources with a status review workflow. Includes a built-in web interface for browsing knowledge and an admin panel for managing the system.

---

## Architecture

### Category System
- Categories are defined in each markdown file's YAML frontmatter (`categories: [cat1, cat2]`)
- Files can live in any folder structure — the folder hierarchy is irrelevant
- Each document can belong to multiple categories
- Files can be indexed even without frontmatter or categories
- If categories are missing, category defaults to `None` (stored as uncategorized internally)
- The `save_knowledge` MCP tool auto-guesses categories via semantic search against existing documents

### Status + Revision Workflow
- Every document has a `status` field: `draft` → `in_review` → `approved`
- A new revision is created only when an `approved` document is modified
- Content edits while status is `draft` or `in_review` update the current revision in place
- Content edits from MCP/UI always reset status to `draft`
- Status-only changes do not create a new revision
- MCP search/retrieval returns the **latest approved revision** of each document
- Admin review queue shows all `draft` and `in_review` documents
- Status changes track reviewer names in `approved_by` / `in_review_by` (stored internally as legacy-compatible fields)

### Revision Storage
- `documents` stores the current snapshot for web/admin editing
- `document_revisions` stores append-only historical snapshots per `file_path`
- Imported markdown revision metadata (if present) is honored; otherwise defaults are used

### Document Metadata (YAML Frontmatter)
```yaml
---
title: Document Title
categories: [python, engineering]   # REQUIRED — determines categorization
date: 2025-01-15                    # OPTIONAL
author: author-name                 # OPTIONAL
approved_by: ''                     # Who approved
in_review_by: ''                    # Who set in_review
status: draft                      # draft | in_review | approved
revision: 3                         # OPTIONAL on import; used if present
---
```

Frontmatter is optional. Missing values are defaulted as:
- `status`: `draft`
- `author`: `Local` (for local files without author metadata)
- `category`: `None`
- `title`: first heading (fallback: filename)
- `date`: current date

---

## Project Structure

```
rag-mcp/
├── pyproject.toml
├── rag-config.yaml            # YAML config (knowledge_dir, data_dir, web settings)
├── .vscode/mcp.json           # VS Code MCP integration config
├── src/
│   └── rag_mcp/
│       ├── __init__.py
│       ├── server.py            # FastMCP entry point + tools/resources/prompts
│       ├── config.py            # YAML config loader with defaults
│       ├── crawler.py           # Flat file discovery (discover_files)
│       ├── indexer.py           # Indexing pipeline with change detection
│       ├── markdown_parser.py   # Frontmatter extraction + heading-based chunking
│       ├── fts_store.py         # SQLite FTS5 wrapper with metadata & categories
│       ├── vector_store.py      # LanceDB + fastembed wrapper
│       └── web/
│           ├── __init__.py
│           ├── app.py           # Starlette web app + REST API routes
│           ├── static/
│           │   ├── css/style.css
│           │   └── js/app.js
│           └── templates/
│               ├── base.html
│               ├── index.html       # Category browser landing page
│               ├── category.html    # Document list for a category
│               ├── document.html    # Document viewer with inline editing
│               ├── search.html      # Search results page
│               └── admin.html       # Admin panel with review queue
├── knowledge/                  # Default knowledge root (configurable)
└── data/                       # Runtime DBs (SQLite + LanceDB)
```

---

## MCP Tools

| Tool | Description |
|------|-------------|
| `search(query, category?, search_type?)` | Hybrid/keyword/semantic search with RRF fusion. Returns only approved documents |
| `get_document(file_path)` | Retrieve full document content by path |
| `get_related(file_path, n?)` | Find semantically similar documents |
| `list_categories()` | List all categories with document counts |
| `browse_category(category)` | List documents in a category with status, author info |
| `save_knowledge(title, content, author, categories?)` | Create new document, auto-guesses categories |
| `update_knowledge(file_path, content, author?)` | Update existing document, resets status to draft |

## MCP Prompts

| Prompt | Description |
|--------|-------------|
| `ask(question)` | Search knowledge base and synthesize an answer |
| `summarize(topic)` | Summarize everything known about a topic |
| `add_frontmatter(file_description)` | Generate correct YAML frontmatter for a new file |

---

## Indexing Pipeline

- **Startup (incremental):** Only indexes new files, skips existing.
- **Admin reindex (force):** Full mtime → blake2b hash comparison. Re-indexes changed files.
- **Change detection:** Two-tier — fast mtime check → content hash diff
- **Skip logic:** Same hash = skip. Same mtime = skip.

---

## Web Interface

### Pages
| Route | Description |
|-------|-------------|
| `GET /` | Category grid with document counts |
| `GET /category/{path}` | Documents in a category with subcategories |
| `GET /document/{path}` | Document viewer — rendered markdown, metadata bar, inline editing, category badges |
| `GET /search?q=...` | Search results with highlighted snippets |
| `GET /admin` | Admin dashboard — stats, review queue, reindex controls |

### REST API
| Route | Method | Description |
|-------|--------|-------------|
| `/api/categories` | GET | Category list as JSON |
| `/api/categories/{path}` | GET | Documents in a category |
| `/api/documents/{path}` | GET | Document content + metadata |
| `/api/documents/{path}/revisions` | GET | Revision history for a document |
| `/api/documents/{path}/revisions/{revision}` | GET | Specific revision content + rendered HTML |
| `/api/documents/{path}/compare?left=X&right=Y` | GET | Side-by-side revision compare payload |
| `/api/documents/{path}/metadata` | PATCH | Update metadata (status, author, categories) |
| `/api/documents/{path}/content` | PUT | Update document content |
| `/api/search` | GET | Search results as JSON |
| `/api/admin/reindex-all` | POST | Full re-index |
| `/api/admin/reindex/{path}` | POST | Re-index a category |
| `/api/admin/export` | GET | Export ZIP with modes: `mode=all|newest|newest_approved` (+ optional `category`) |
| `/api/admin/stats` | GET | System statistics |

### UI Design
- **Theme:** Terminal developer aesthetic — dark (#0a0a0a), JetBrains Mono font
- **Colors:** Green (#22c55e) for approved, yellow (#eab308) for in_review, gray (#999) for draft
- **Features:** Category badges, inline document editing, status dropdown, revision history + split compare, related documents sidebar

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| MCP framework | FastMCP (mcp[cli] v1.26.0) |
| Keyword search | SQLite FTS5 (porter stemmer, BM25 ranking) |
| Semantic search | LanceDB v0.29.2 + fastembed v0.7.4 (all-MiniLM-L6-v2, 384-dim) |
| Web framework | Starlette + Jinja2 (SSR) |
| Web server | uvicorn (daemon thread alongside MCP) |
| Package manager | uv |
| Python | >=3.10, <3.14 |

---

## Key Decisions

| Decision | Choice |
|----------|--------|
| Categories | Frontmatter-based (`categories: [...]`), not folder-based |
| Multi-category | Each document can belong to multiple categories |
| File discovery | Flat — all .md files found recursively regardless of folder structure |
| Status workflow | draft → in_review → approved, with reviewer tracking |
| Search visibility | MCP tools return latest approved revision per document |
| Indexing | Startup-only incremental, admin force reindex available |
| Missing categories | Files without `categories` frontmatter are skipped with warning |
| Metadata requirement | Frontmatter is optional; missing values default in parser/DB |
| Export behavior | Export always writes complete frontmatter metadata (including `revision`) |
| Document creation | `save_knowledge` auto-guesses category via semantic search |
