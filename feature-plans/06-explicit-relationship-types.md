# Feature Plan: Explicit Relationship Types (Knowledge Graph)

**Priority**: High  
**Depends On**: Wikilinks & Backlinks (feature #1) — extends the link concept with typed semantics  
**Foundation For**: Smart Capture (feature #8) — auto-relate uses the same `document_relationships` table  
**Theory**: Linked knowledge structures — typed relationships enable graph reasoning and knowledge paths

---

## Overview

Add structured, typed relationships between documents via frontmatter. Seven relationship types (`explains`, `extends`, `contradicts`, `cites`, `prerequisite`, `supersedes`, `related_to`) allow the knowledge base to function as a navigable graph. Includes BFS shortest-path traversal, deprecation notices for superseded docs, and a sidebar showing both outgoing and incoming relationships.

---

## 1. Parser Changes — `markdown_parser.py`

### New Dataclass

```python
@dataclass
class DocumentRelationship:
    target: str   # file_path or document title
    type: str     # one of the 7 vocabulary types
    note: str = ""
```

### DocumentMeta Extension

```python
@dataclass
class DocumentMeta:
    # ... existing fields ...
    relationships: list[DocumentRelationship] = field(default_factory=list)
```

### Frontmatter Syntax

```yaml
relationships:
  - target: "python-generators.md"
    type: "explains"
    note: "Prerequisite concept"
  - target: "old-deployment.md"
    type: "supersedes"
```

### Parse Logic

- Extract `relationships` list from frontmatter YAML
- Validate each `type` against the allowed vocabulary (reject or warn on invalid types)
- Construct `DocumentRelationship` instances and attach to `DocumentMeta.relationships`

---

## 2. Database Schema — `fts_store.py`

### New Table: `document_relationships`

```sql
CREATE TABLE IF NOT EXISTS document_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    target_id INTEGER,
    source_file_path TEXT NOT NULL,
    target_file_path TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(source_id) REFERENCES documents(id),
    FOREIGN KEY(target_id) REFERENCES documents(id),
    UNIQUE(source_file_path, target_file_path, relationship_type)
);

CREATE INDEX IF NOT EXISTS idx_relationships_source
    ON document_relationships(source_file_path);
CREATE INDEX IF NOT EXISTS idx_relationships_target
    ON document_relationships(target_file_path);
CREATE INDEX IF NOT EXISTS idx_relationships_type
    ON document_relationships(relationship_type);
```

No migration needed — purely additive (new table creation in `_init_schema()`).

---

## 3. FTS Store Methods — `fts_store.py`

### `upsert_relationships(source_file_path, relationships)`

- Look up `source_id` from `documents` table
- Delete all existing relationships for this source (full replace on each index cycle)
- Insert new relationships, resolving `target_id` when the target document exists
- Use `ON CONFLICT ... DO UPDATE SET note, updated_at` for the unique constraint

### `get_outgoing_relationships(file_path) -> list[dict]`

- Query `document_relationships WHERE source_file_path = ?`
- Enrich each result with target document title and status (via `get_document()`)
- Return list of `{source, target, type, note, target_title, target_status}`

### `get_incoming_relationships(file_path) -> list[dict]`

- Query `document_relationships WHERE target_file_path = ?`
- Enrich with source document title
- Include `inverse_type` (e.g., `explains` → `explained_by`)
- Return list of `{source, target, type, note, source_title, inverse_type}`

### `find_knowledge_path(start, end, max_depth=5) -> list[dict] | None`

- BFS traversal through outgoing relationships
- Track visited set to prevent cycles
- Return ordered list of steps: `{file_path, title, type, distance}`
- Return `None` if no path exists within `max_depth`

### `delete_relationships(file_path)`

- Delete all rows where `source_file_path = ? OR target_file_path = ?`
- Called when a document is removed from the index

### Helper: `_inverse_relationship_type(rel_type) -> str`

| Type | Inverse |
|------|---------|
| `explains` | `explained_by` |
| `extends` | `extended_by` |
| `contradicts` | `contradicted_by` |
| `cites` | `cited_by` |
| `prerequisite` | `required_by` |
| `supersedes` | `superseded_by` |
| `related_to` | `related_to` |

---

## 4. Indexer Changes — `indexer.py`

### In `_index_file()`

After existing document indexing:

```python
if parsed.meta.relationships:
    relationship_dicts = [
        {"target": rel.target, "type": rel.type, "note": rel.note}
        for rel in parsed.meta.relationships
    ]
    self.fts.upsert_relationships(parsed.meta.file_path, relationship_dicts)
else:
    self.fts.delete_relationships(parsed.meta.file_path)
```

### Relationship Validation

```python
def validate_relationships(self, relationships) -> list[str]:
```

Check that all relationship targets exist as indexed documents. Return list of warning strings for missing targets. Warnings are logged but do not block indexing.

---

## 5. MCP Server Enhancements — `server.py`

### New Tool: `get_knowledge_path`

```python
@mcp.tool()
async def get_knowledge_path(
    start_file_path: str,
    end_file_path: str,
    max_depth: int = 5
) -> str:
```

Find shortest relationship path between two documents. Returns human-readable chain with relationship type icons (→ explains, ← requires, ⟿ replaces, etc.).

### Enhanced: `get_related()`

- Fetch explicit relationships (outgoing + incoming) first
- Group by type, show direction arrows (→ outgoing, ← incoming)
- Fill remaining slots with semantic similarity matches
- Explicit relationships always prioritized over semantic matches

### Enhanced: `get_document()`

- Check incoming relationships for `type == "supersedes"`
- If found, prepend a deprecation notice: "⚠️ DEPRECATED — This document has been superseded by [title]"

---

## 6. Web UI Changes

### REST Endpoint — `app.py`

| Route | Method | Description |
|-------|--------|-------------|
| `/api/documents/{path}/relationships` | GET | Returns `{outgoing: [...], incoming: [...]}` |

### Document Page — `document.html`

**Deprecation banner** (after title, before content):

```html
{% if superseded_by %}
<div class="deprecation-notice">
    ⚠️ Deprecated: Superseded by <a href="...">{{ superseded_by.source_title }}</a>
</div>
{% endif %}
```

**Sidebar sections** (in order):

1. **Relationships** — outgoing, grouped by type (explains, extends, prerequisite, etc.)
2. **Referenced by** — incoming relationships with inverse type labels
3. **Semantically Similar** — existing semantic matches (moved below explicit relationships)

Each relationship link shows:
- Direction arrow (→/←)
- Target document title (clickable)
- Status badge (approved/draft/in_review)
- Optional note in italic below

### CSS

- `.deprecation-notice` — red-left-bordered warning banner
- `.relationship-group` — grouped by type with uppercase label
- `.relationship-item` — card-style with hover effect
- `.referenced-item` — similar styling to outgoing but with ← arrow
- Status badges color-coded: green (approved), blue (draft), amber (in_review)

---

## 7. Relationship Vocabulary

| Type | Meaning | Example |
|------|---------|---------|
| `explains` | Foundational concept for this doc | "Python Generators" explains "Async Patterns" |
| `extends` | Builds on or adds to | "Advanced Docker" extends "Docker Basics" |
| `contradicts` | Disagrees with or corrects | "New Auth approach" contradicts "Old Auth" |
| `cites` | References or quotes | "Architecture Decision" cites "RFC 7231" |
| `prerequisite` | Must read before this | "SQL Basics" is prerequisite for "Query Optimization" |
| `supersedes` | Replaces or deprecates | "Deploy v2" supersedes "Deploy v1" |
| `related_to` | Loosely connected | "CI Pipeline" related_to "Testing Strategy" |

---

## 8. Test Strategy

### Parser Tests
- `test_parse_relationships_from_frontmatter` — Valid YAML extraction
- `test_parse_relationships_missing_target` — Graceful handling
- `test_parse_invalid_relationship_type` — Rejection/warning

### FTS Store Tests
- `test_upsert_relationships` — Store and retrieve
- `test_get_outgoing_relationships` — Enriched with target metadata
- `test_get_incoming_relationships` — Inverse type computed correctly
- `test_find_knowledge_path` — 2-hop path, circular refs, no-path, max-depth
- `test_delete_relationships` — Cascading cleanup

### Indexer Tests
- `test_index_file_with_relationships` — Relationships processed during sync
- `test_relationship_validation` — Missing targets produce warnings

### Server/MCP Tests
- `test_get_knowledge_path_tool` — Formatted output
- `test_get_related_with_explicit_relationships` — Explicit prioritized
- `test_get_document_deprecation_notice` — Supersession detected

### Web Tests
- `test_api_document_relationships` — REST endpoint returns correct JSON

---

## Implementation Sequence

1. Parser: Add `DocumentRelationship` dataclass and frontmatter extraction
2. Database: Create `document_relationships` table with indexes
3. FTS Store: Implement upsert, query (outgoing/incoming), BFS path, delete
4. Indexer: Wire relationship processing into `_index_file()`
5. Server: Add `get_knowledge_path` tool, enhance `get_related` and `get_document`
6. Web UI: Add REST endpoint, deprecation banner, relationship sidebar sections
7. Tests: Full coverage across all layers

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| 7 fixed relationship types | Enough to express common knowledge connections without overcomplicating |
| Bidirectional queries (outgoing + incoming) | Documents should know what references them, not just what they reference |
| BFS for shortest path | Simple, correct for unweighted graphs, bounded by `max_depth` |
| Full replace on index | Simpler than diff — delete all relationships for a source, re-insert from frontmatter |
| Graceful unresolved targets | Allow relationships to targets not yet indexed (future docs, external refs) |
| Supersession → deprecation notice | Clear signal that knowledge has been replaced, prevents stale usage |
