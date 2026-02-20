# Feature Plan: Smart Capture with Context

**Priority**: Medium  
**Depends On**: Explicit Relationship Types (feature #6) — reuses `document_relationships` table  
**Foundation For**: Self-organizing knowledge base, orphan detection  
**Theory**: Associative knowledge capture — new knowledge should automatically connect to existing knowledge

---

## Overview

Enhance `save_knowledge()` to automatically discover and store relationships when a new document is created. The system finds semantically similar documents, supports explicit parent-child links, auto-suggests categories, writes relationships back to frontmatter, and identifies orphan documents with no connections.

---

## 1. Parser Changes — `markdown_parser.py`

### New `DocumentMeta` Fields

```python
@dataclass
class DocumentMeta:
    # ... existing fields ...
    related_documents: list[str] = field(default_factory=list)   # auto-discovered file paths
    parent_doc: str | None = None                                 # explicit parent relationship
```

### Frontmatter Syntax

```yaml
---
title: New Document
author: AI-Agent
categories: [python, patterns]
parent_doc: "design-patterns.md"
related_documents:
  - "generator-functions.md"
  - "comprehensions.md"
---
```

### Parse Logic

- Extract `related_documents` from frontmatter (list of strings or comma-separated string)
- Extract `parent_doc` (single string or null)
- Both fields are optional — existing documents work without them

---

## 2. Database Schema — `fts_store.py`

### Reuses `document_relationships` Table (from Feature #6)

The same table created by Explicit Relationship Types. Smart Capture adds three relationship types to the vocabulary:

| Type | Meaning |
|------|---------|
| `related` | Auto-discovered via semantic similarity |
| `parent` | Explicit parent document |
| `child` | Inverse of parent |

These coexist with the typed relationships from Feature #6 (`explains`, `extends`, etc.).

### New Table: `relationship_discovery`

```sql
CREATE TABLE IF NOT EXISTS relationship_discovery (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    related_count INTEGER DEFAULT 0,
    has_parent BOOLEAN DEFAULT 0,
    child_count INTEGER DEFAULT 0,
    is_orphan BOOLEAN DEFAULT 0,
    last_analyzed TEXT NOT NULL,
    FOREIGN KEY(file_path) REFERENCES documents(file_path)
);

CREATE INDEX IF NOT EXISTS idx_discovery_orphan
    ON relationship_discovery(is_orphan);
```

Tracks per-document discovery metadata for reporting and orphan detection.

---

## 3. FTS Store Methods — `fts_store.py`

### `upsert_relationship(source, target, type, similarity_score)`

Store a single relationship with similarity score. Uses `ON CONFLICT` upsert on the unique constraint `(source_file_path, target_file_path, relationship_type)`.

### `get_relationships(file_path, type?, direction)`

Query relationships by direction (`outgoing`, `incoming`, `both`) with optional type filter. Results ordered by `similarity_score DESC`.

### `update_discovery_metadata(file_path, is_orphan, related_count, has_parent, child_count)`

Upsert into `relationship_discovery`. Tracks whether a document is an orphan and how many connections it has.

### `get_orphan_documents() -> list[dict]`

```sql
SELECT d.file_path, d.title, d.category, d.date, d.author, rd.last_analyzed
FROM documents d
LEFT JOIN relationship_discovery rd ON d.file_path = rd.file_path
WHERE rd.is_orphan = 1 OR rd.id IS NULL
ORDER BY d.title
```

Returns documents with no relationships — either explicitly marked as orphans or never analyzed.

### `get_documents_by_relationship_type(type) -> list[dict]`

Get all document pairs of a specific relationship type, ordered by similarity score.

---

## 4. Vector Store Enhancement — `vector_store.py`

### Extended `search_similar()` with Threshold

```python
def search_similar(
    self,
    file_path: str,
    category: str,
    n_results: int = 5,
    similarity_threshold: float = 0.3,   # NEW: filter by distance
) -> list[VectorResult]:
```

Skip results where `distance > similarity_threshold`. A threshold of 0.4 distance ≈ 0.82 cosine similarity — a sensible default for "meaningfully related."

---

## 5. Enhanced `save_knowledge` Tool — `server.py`

### Updated Signature

```python
@mcp.tool()
async def save_knowledge(
    title: str,
    content: str,
    author: str,
    categories: list[str] | None = None,
    parent_doc: str | None = None,       # NEW
    skip_auto_relate: bool = False,      # NEW
) -> str:
```

### Workflow (6 Steps)

1. **Auto-detect category** — If `categories` is None, find the best match via semantic search of existing categories. If no match, ask the user to specify.

2. **Create document** — Call `indexer.add_document()` as before.

3. **Find related documents** — Unless `skip_auto_relate=True`, run `vectors.search_similar()` with `n_results=5` and `similarity_threshold=0.4`. Store each match as a `related` relationship in `document_relationships`.

4. **Handle parent_doc** — If specified and exists:
   - Store `parent` relationship: new doc → parent
   - Store `child` relationship: parent → new doc
   - If parent not found, include warning in response

5. **Update frontmatter** — Write `related_documents` and `parent_doc` back to the markdown file on disk.

6. **Update discovery metadata** — Mark `is_orphan=True` if no related docs found and no parent specified.

### Response Format

```
Knowledge saved successfully!
- **Title:** Python Decorators
- **Categories:** python
- **Author:** AI-Agent
- **File:** python/python-decorators.md

**Related Documents Found (3)**:
- Python Functions (92% similar)
- Advanced Python Patterns (85% similar)
- Generator Functions (81% similar)

**Relationship:**
- Parent: python/python-basics.md
```

Orphan warning when applicable:
```
⚠️ No related documents found. Consider the parent_doc parameter.
```

---

## 6. New MCP Tools — `server.py`

### `find_orphan_documents(category?, include_analysis?)`

```python
@mcp.tool()
async def find_orphan_documents(
    category: str | None = None,
    include_analysis: bool = False,
) -> str:
```

- Lists all documents with `is_orphan=True` or never analyzed
- Optional category filter
- When `include_analysis=True`, runs semantic search for each orphan and suggests potential relationships

### `get_related_documents(file_path, relationship_type?, direction?)`

```python
@mcp.tool()
async def get_related_documents(
    file_path: str,
    relationship_type: str | None = None,
    direction: str = "outgoing",
) -> str:
```

- Returns all relationships for a document, grouped by type
- Shows similarity scores for auto-discovered relationships

---

## 7. Indexer Enhancement — `indexer.py`

### `analyze_all_document_relationships() -> dict`

Batch operation that:
1. Iterates all indexed documents
2. Runs semantic search for each
3. Stores discovered relationships
4. Updates `relationship_discovery` metadata
5. Returns stats: `{analyzed, orphans, related_pairs}`

Useful for initial setup or periodic re-analysis. Called via admin endpoint or CLI.

### `find_best_category(title, content) -> str | None`

Searches all category tables in LanceDB for the most similar document. Returns the category of the best match, or `None` if nothing is close enough.

---

## 8. Frontmatter Update Helper

```python
def _update_document_relationships_in_frontmatter(
    ctx, file_path, related_documents, parent_doc
) -> bool:
```

- Loads the markdown file using `frontmatter` library
- Updates `related_documents` and `parent_doc` fields in metadata
- Writes back to disk
- Does NOT trigger re-indexing (relationships stored in DB separately)

---

## 9. Test Strategy

### Parser Tests
- `test_parse_related_documents` — List extraction from frontmatter
- `test_parse_parent_doc` — Single string extraction
- `test_parse_related_docs_empty` — Defaults to empty list
- `test_parse_related_docs_comma_separated` — Handle string format

### FTS Store Tests
- `test_upsert_relationship` — Store and retrieve
- `test_get_relationships_outgoing` — Direction filter
- `test_get_relationships_incoming` — Inverse direction
- `test_get_orphan_documents` — Correct identification
- `test_update_discovery_metadata` — Upsert behavior

### Vector Store Tests
- `test_search_similar_with_threshold` — Results filtered by distance
- `test_search_similar_no_matches` — Empty results when nothing similar

### Server/MCP Tests
- `test_save_knowledge_auto_relate` — Related docs discovered and stored
- `test_save_knowledge_with_parent` — Parent-child bidirectional relationships
- `test_save_knowledge_parent_not_found` — Warning in response
- `test_save_knowledge_skip_auto_relate` — No relationships created
- `test_save_knowledge_auto_category` — Category auto-detected
- `test_find_orphan_documents` — Correct orphan list
- `test_find_orphan_documents_with_analysis` — Suggestions provided

### Indexer Tests
- `test_analyze_all_relationships` — Batch analysis stats
- `test_find_best_category` — Correct category returned

---

## Implementation Sequence

1. Parser: Add `related_documents` and `parent_doc` fields to `DocumentMeta`
2. Database: Create `relationship_discovery` table (assumes `document_relationships` from Feature #6)
3. FTS Store: Implement relationship CRUD and orphan detection methods
4. Vector Store: Add `similarity_threshold` parameter to `search_similar()`
5. Server: Enhance `save_knowledge()` with auto-relate workflow
6. Server: Add `find_orphan_documents` and `get_related_documents` tools
7. Indexer: Add `analyze_all_document_relationships()` and `find_best_category()`
8. Helper: Implement frontmatter update for writing relationships back to disk
9. Tests: Full coverage across all layers

---

## Files to Create

| File | Purpose |
|------|---------|
| (none) | All changes in existing files |

## Files to Modify

| File | Changes |
|------|---------|
| `src/rag_mcp/markdown_parser.py` | Add `related_documents`, `parent_doc` to `DocumentMeta` |
| `src/rag_mcp/fts_store.py` | Add `relationship_discovery` table, orphan queries |
| `src/rag_mcp/vector_store.py` | Add `similarity_threshold` to `search_similar()` |
| `src/rag_mcp/server.py` | Enhance `save_knowledge`, add orphan/related tools |
| `src/rag_mcp/indexer.py` | Add `analyze_all_document_relationships()`, `find_best_category()` |

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Auto-relate on save (not on index) | Relationships created at capture time, when context is freshest |
| `skip_auto_relate` flag | Escape hatch for bulk imports or manual control |
| Similarity threshold 0.4 | Balances precision (not too loose) with recall (catches meaningful connections) |
| Write relationships to frontmatter | Makes relationships visible in the markdown files, not just the DB |
| Orphan detection via `relationship_discovery` | Separate tracking table avoids expensive JOINs on every query |
| Bidirectional parent/child | Parent knows its children, children know their parent |
| Reuse `document_relationships` table | Unified relationship storage regardless of how relationships are created |
