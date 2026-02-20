# RAG MCP Server

A RAG (Retrieval-Augmented Generation) server built on the [Model Context Protocol](https://modelcontextprotocol.io). It indexes markdown files into SQLite (keyword search) and LanceDB (semantic search), exposes MCP tools, and includes a Starlette web/admin UI.

## Features

- Frontmatter is optional; metadata defaults are handled in the DB.
- Documents are identified by filename only.
- Search uses SQLite FTS5 + LanceDB and returns only approved document content.
- Revisions are tracked in SQLite and can be compared in the web UI.
- Status workflow: `draft` → `in_review` → `approved`.
- Embeddings are stored only for currently approved documents.
- Export modes: `all`, `newest`, `newest_approved`.
- Reindex cleanup removes deleted docs, orphan revisions, and stale vectors.

## Prerequisites

- **Python 3.10 – 3.13** (3.14 not yet supported)
- **[uv](https://docs.astral.sh/uv/)** package manager
- **Microsoft Visual C++ Redistributable** (Windows only) — install via `winget install Microsoft.VCRedist.2015+.x64`

## Quick Start

### 1. Install dependencies

```bash
uv sync
```

### 2. Add knowledge

Place markdown files anywhere under the knowledge directory. Folder structure doesn't matter — categorization is determined by the `categories` field in YAML frontmatter:

```markdown
---
title: My Document Title
categories: [python, engineering]
---

# Content starts here
```

Frontmatter is optional. When fields are missing, defaults are used:

- `status`: `draft`
- `author`: `Local` (for local files without author metadata)
- `category`: `None`
- `title`: first heading (fallback: filename)
- `date`: current date

Optional metadata that will be used if provided on import:

```markdown
---
date: 2025-01-15
author: Your Name
status: approved
approved_by: AB
in_review_by: ''
revision: 7
---
```

If optional metadata is missing, defaults are stored in the DB and export will include complete metadata.

### 3. Run the server

**As an MCP server** (stdio transport, for use with Claude Desktop, VS Code, etc.):

```bash
uv run rag-mcp
```

**With MCP Inspector** (for testing tools interactively):

```bash
uv run mcp dev src/rag_mcp/server.py
```

The web interface starts automatically at **http://127.0.0.1:8765**.

## Configuration

All configuration goes in `rag-config.yaml`. Every setting is optional:

```yaml
# Path to the folder containing your markdown knowledge files.
knowledge_dir: ./knowledge

# Path to the data directory for SQLite and LanceDB storage.
data_dir: ./data

# Web interface settings.
web:
  enabled: true
  host: 127.0.0.1
  port: 8765
  # Optional: set a token to protect the admin panel.
  # admin_token: my-secret-token
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `search(query, category?, search_type?)` | Hybrid/keyword/semantic search with RRF fusion (approved docs only) |
| `get_document(file_path)` | Retrieve full document content by path (latest approved revision) |
| `get_related(file_path, n?)` | Find semantically similar documents based on the latest approved revision |
| `list_categories()` | List all categories with document counts |
| `browse_category(category)` | List docs in a category with status/author info |
| `save_knowledge(title, content, author, categories?)` | Create new document — auto-guesses categories via semantic search if not provided |
| `update_knowledge(file_path, content, author?)` | Update existing document, resets status to draft |

## MCP Prompts

| Prompt | Description |
|--------|-------------|
| `ask(question)` | Search and synthesize an answer from the knowledge base |
| `summarize(topic)` | Summarize everything known about a topic |

## Web Interface

| Page | URL |
|------|-----|
| Category browser | `http://127.0.0.1:8765/` |
| Category documents | `http://127.0.0.1:8765/category/{name}` |
| Document viewer | `http://127.0.0.1:8765/document/{path}` |
| Search | `http://127.0.0.1:8765/search?q=your+query` |
| Admin panel | `http://127.0.0.1:8765/admin` |

### Export Modes

Use `GET /api/admin/export` with:

- `mode=all` — all revisions of all documents
- `mode=newest` — newest revision per document
- `mode=newest_approved` — newest approved revision per document
- `category=<name>` (optional) — limit any mode to a category

For `mode=all`, only the latest revision is exported as `.md`; older revisions are exported as `.mdx` archive files so re-importing does not create duplicate indexed documents.
Exports are always flat (no folder hierarchy).

If you need full recovery, set `knowledge_dir` to the extracted export and run a full reindex; the `.mdx` revision archives are merged back into the original document revision history.

Reindex cleanup behavior is strict: deleted files remove both current documents and their full revision history from SQLite, orphaned revision rows are purged, and vector data is kept only for currently approved documents.

## Using with VS Code

Add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "rag-mcp": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "rag-mcp"],
      "cwd": "${workspaceFolder}"
    }
  }
}
```

## Using with Claude Desktop

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "rag-knowledge": {
      "command": "uv",
      "args": ["--directory", "/path/to/rag-mcp", "run", "rag-mcp"]
    }
  }
}
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| MCP framework | FastMCP (mcp[cli] v1.26.0) |
| Keyword search | SQLite FTS5 (porter stemmer, BM25 ranking) |
| Semantic search | LanceDB v0.29.2 + fastembed v0.7.4 (all-MiniLM-L6-v2, 384-dim) |
| Web framework | Starlette + Jinja2 (server-side rendered) |
| Web server | uvicorn (daemon thread alongside MCP) |
| Package manager | uv |

## Development

```bash
# Run tests
uv run pytest -q

# Start with MCP Inspector for interactive testing
uv run mcp dev src/rag_mcp/server.py
```

## License

MIT
