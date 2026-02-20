# Feature Plan: Wikilinks & Backlinks

**Priority**: Critical  
**Foundation For**: Knowledge graph, explicit relationship types, graph visualization  
**Theory**: Zettelkasten — explicit, author-intentional connections vs. implicit semantic similarity

---

## Overview

Add `[[document-name]]` wikilink syntax support. When authors write `[[Python Generators]]` in a document, the engine parses the link, resolves it to a file path, stores the relationship, and enables bidirectional navigation. This transforms a flat document collection into a connected knowledge graph.

---

## 1. Parser Changes

**File**: `src/rag_mcp/markdown_parser.py`

### New regex and extraction function

```python
WIKILINK_RE = re.compile(r'\[\[([^\[\]]+)\]\]')

def _extract_wikilinks(body: str) -> list[str]:
    """Extract all [[...]] wikilinks from markdown body, deduplicated, order-preserved."""
    seen = set()
    results = []
    for match in WIKILINK_RE.finditer(body):
        text = match.group(1).strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            results.append(text)
    return results
```

### ParsedDocument changes

Add `links` field to the `ParsedDocument` dataclass:

```python
@dataclass
class ParsedDocument:
    meta: DocumentMeta
    content: str
    chunks: list[Chunk]
    links: list[str] = field(default_factory=list)  # NEW: wikilinks found in body
```

### parse_markdown() modification

After chunking, extract wikilinks:

```python
chunks = _split_by_headings(body, fp_str, primary_category, title)
links = _extract_wikilinks(body)  # NEW
return ParsedDocument(meta=meta, content=body, chunks=chunks, links=links)
```

---

## 2. Database Schema

**File**: `src/rag_mcp/fts_store.py`

### New table: `document_links`

```sql
CREATE TABLE IF NOT EXISTS document_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file_path TEXT NOT NULL,
    target_file_path TEXT NOT NULL,
    target_title TEXT NOT NULL DEFAULT '',
    link_text TEXT NOT NULL,
    is_valid INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (source_file_path) REFERENCES documents(file_path) ON DELETE CASCADE,
    UNIQUE(source_file_path, target_file_path, link_text)
);

CREATE INDEX IF NOT EXISTS idx_links_source ON document_links(source_file_path);
CREATE INDEX IF NOT EXISTS idx_links_target ON document_links(target_file_path);
CREATE INDEX IF NOT EXISTS idx_links_valid ON document_links(is_valid);
```

### Cleanup trigger

```sql
CREATE TRIGGER IF NOT EXISTS document_links_delete
AFTER DELETE ON documents
BEGIN
    DELETE FROM document_links
    WHERE source_file_path = OLD.file_path OR target_file_path = OLD.file_path;
END;
```

---

## 3. FTS Store Methods

**File**: `src/rag_mcp/fts_store.py`

### `upsert_links(file_path, outbound_links, normalized_targets)`

- Delete existing links for `source_file_path`
- INSERT each link with resolved target path and validity flag
- ON CONFLICT update `target_title` and `is_valid`

### `get_backlinks(file_path, limit=10) -> list[dict]`

Query `document_links` JOIN `documents` WHERE `target_file_path = ?` AND `is_valid = 1`. Returns `[{file_path, title, snippet, link_text}]`. Snippet extracted around the wikilink occurrence in the source content.

### `get_backlink_count(file_path) -> int`

`SELECT COUNT(DISTINCT source_file_path) WHERE target_file_path = ? AND is_valid = 1`

### `get_outbound_links(file_path) -> list[str]`

`SELECT DISTINCT target_file_path WHERE source_file_path = ? AND is_valid = 1`

### `get_link_validation_report() -> list[dict]`

`SELECT source_file_path, link_text, target_file_path WHERE is_valid = 0` — all broken links across the knowledge base.

---

## 4. Indexer Changes

**File**: `src/rag_mcp/indexer.py`

### New helper: `_normalize_wikilink_target(link_text, all_files) -> (path, exists)`

Matching strategy:
- Case-insensitive: `[[Python]]` → `python.md`
- Spaces to dashes: `[[Async Patterns]]` → `async-patterns.md`
- Strip special chars for matching

Returns `(logical_file_key, True)` if found, `(guessed_path, False)` if not.

### _index_file() modification

After upserting the document and syncing vectors, process wikilinks:

1. If `parsed.links` is non-empty:
   - Build `all_files` dict from `discover_files()`
   - Normalize each link text to a target path
   - Call `fts.upsert_links(file_path, links, normalized_targets)`
   - Log warnings for broken links: `logger.warning("File %s has broken wikilinks: [[%s]]", ...)`

---

## 5. MCP Server Tools

**File**: `src/rag_mcp/server.py`

### New tool: `get_backlinks(file_path: str) -> str`

Returns formatted list of documents referencing this file via wikilinks. Includes title, path, and context snippet for each backlink.

### New tool: `get_link_validation_report() -> str`

Returns all broken wikilinks grouped by source document. Useful for knowledge base maintenance.

### Enhanced `get_related(file_path, n=5) -> str`

Priority ranking:
1. **Outbound links** (docs THIS links to) — Score 100
2. **Backlinks** (docs linking to THIS) — Score 80
3. **Semantic matches** (vector similarity) — Score 50 minus distance

Labels each result: `→ links to`, `← linked from`, `~ similar`.

---

## 6. Web UI

**File**: `src/rag_mcp/web/app.py`

### New route: `GET /api/documents/{path}/backlinks`

Returns JSON: `{file_path, backlink_count, backlinks: [{file_path, title, snippet, link_text}]}`

### page_document() modification

Fetch backlinks and backlink count, pass to template context.

**File**: `src/rag_mcp/web/templates/document.html`

### Backlinks sidebar section

After the "related" sidebar section, add a "referenced by (N)" section listing documents that link to this one.

```html
{% if backlink_count > 0 %}
<div class="sidebar-section">
    <h3 class="sidebar-section__title">referenced by ({{ backlink_count }})</h3>
    <ul class="related-list">
        {% for bl in backlinks %}
        <li>
            <a href="/document/{{ bl.file_path }}" class="related-list__link">
                <span class="related-list__title">{{ bl.title }}</span>
                <span class="related-list__snippet">via [[{{ bl.link_text }}]]</span>
            </a>
        </li>
        {% endfor %}
    </ul>
</div>
{% endif %}
```

---

## 7. Validation

During indexing, when a wikilink target cannot be resolved:
- Store with `is_valid = 0`
- Log `WARNING` with source file and broken link text
- On subsequent re-index, re-validate — if target now exists, update to `is_valid = 1`

The `get_link_validation_report()` MCP tool exposes all broken links for maintenance.

---

## 8. Test Strategy

### Parser tests (`test_parser.py`)
- `test_extract_wikilinks_single` — `"Text [[Python]] end"` → `["Python"]`
- `test_extract_wikilinks_multiple` — Multiple links, deduplicated
- `test_extract_wikilinks_spaces` — `"[[Async Patterns]]"` preserved
- `test_no_wikilinks` — Plain text returns `[]`

### FTS store tests (`test_fts_store.py`)
- `test_upsert_links` — Store and retrieve outbound links
- `test_get_backlinks` — Doc A → Doc B, verify B's backlinks include A
- `test_backlink_count` — Correct count with multiple sources
- `test_broken_link_tracking` — `is_valid=0` for missing targets
- `test_link_cleanup_on_delete` — Trigger removes links when doc deleted

### Indexer tests (`test_indexer.py`)
- `test_normalize_case_insensitive` — `[[PYTHON]]` matches `python.md`
- `test_normalize_spaces_to_dash` — `[[Async Patterns]]` matches `async-patterns.md`
- `test_normalize_not_found` — Returns `(guessed_path, False)`

### Integration tests
- `test_end_to_end_wikilink_flow` — Create docs with links, index, verify backlinks
- `test_broken_link_recovery` — Broken link becomes valid when target created

---

## Implementation Sequence

1. Parser: Add `_extract_wikilinks()` and `links` field
2. FTS Store: Create `document_links` table, add all link methods
3. Indexer: Add normalization helper, wire into `_index_file()`
4. Server: Add `get_backlinks` and `get_link_validation_report` tools, enhance `get_related`
5. Web: Add backlinks endpoint and sidebar display
6. Tests: Full test coverage
