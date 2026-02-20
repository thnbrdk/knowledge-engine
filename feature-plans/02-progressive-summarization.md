# Feature Plan: Progressive Summarization

**Priority**: Critical  
**Foundation For**: Tier-based search, express workflow, bandwidth-aware retrieval  
**Theory**: Second Brain (Tiago Forte) — CODE workflow (Capture → Organize → **Distill** → Express)

---

## Overview

Add three-tier summary fields and highlight extraction. Documents can declare summaries at increasing levels of detail — a one-liner, key bullet points, and a full paragraph. Authors mark key passages with `==highlighted text==` syntax. Search and retrieval can operate at different abstraction levels.

---

## 1. Parser Changes

**File**: `src/rag_mcp/markdown_parser.py`

### New fields on `DocumentMeta`

```python
summary_short: str = ""                           # Tier 1: one-liner
summary_points: list[str] = field(default_factory=list)  # Tier 2: bullet points
summary: str = ""                                 # Tier 3: paragraph
highlights: list[str] = field(default_factory=list)      # ==marked text==
```

### Highlight extraction

```python
_HIGHLIGHT_RE = re.compile(r"==([^=]+)==", re.MULTILINE)

def _extract_highlights(body: str) -> list[str]:
    """Extract ==highlighted text== from markdown, deduplicated, order-preserved."""
    seen = set()
    results = []
    for match in _HIGHLIGHT_RE.finditer(body):
        text = match.group(1).strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            results.append(text)
    return results
```

### Summary points parsing

```python
def _parse_summary_points(value) -> list[str]:
    """Parse summary_points from YAML list or comma-separated string."""
    if isinstance(value, list):
        return [str(item).strip() for item in value if item]
    if isinstance(value, str):
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            return yaml.safe_load(value) or []
        return [item.strip() for item in value.split(",") if item.strip()]
    return []
```

### parse_markdown() modification

Extract summary fields from frontmatter and highlights from body:

```python
summary_short = str(fm.get("summary_short", "")).strip()
summary_points = _parse_summary_points(fm.get("summary_points", []))
summary_full = str(fm.get("summary", "")).strip()
highlights = _extract_highlights(body)
```

Pass to `DocumentMeta` constructor.

---

## 2. Frontmatter Format

```yaml
---
title: Docker Container Networking
categories: [devops, docker]
summary_short: "How Docker containers communicate via bridge, host, and overlay networks"
summary_points:
  - "Bridge networks isolate containers on the same host"
  - "Host networking shares the host's network stack"
  - "Overlay networks span multiple Docker hosts"
summary: "Docker provides three primary networking modes. Bridge networks create isolated..."
---

This document explains ==container networking== and how to configure ==overlay networks==.
```

---

## 3. Database Schema

**File**: `src/rag_mcp/fts_store.py`

### New columns on `documents` table

```sql
summary_short TEXT NOT NULL DEFAULT '',
summary_points TEXT NOT NULL DEFAULT '[]',   -- JSON array
summary TEXT NOT NULL DEFAULT '',
highlights TEXT NOT NULL DEFAULT '[]'         -- JSON array
```

### Same columns on `document_revisions` table

Mirror the four columns so revision snapshots preserve summaries.

### Migration

In `_migrate_schema()`, check existing columns via `PRAGMA table_info` and `ALTER TABLE ADD COLUMN` for each missing column. Apply to both `documents` and `document_revisions`.

---

## 4. FTS Store Changes

**File**: `src/rag_mcp/fts_store.py`

### `upsert_document()` modifications

Add parameters: `summary_short`, `summary_points`, `summary`, `highlights`. Serialize lists as JSON for storage. Include in both INSERT and UPDATE statements.

### `_upsert_revision_snapshot()` modifications

Pass summary fields through to revision snapshots.

### `get_document()` modifications

Parse JSON fields on retrieval:
```python
d["summary_points"] = json.loads(d.get("summary_points") or "[]")
d["highlights"] = json.loads(d.get("highlights") or "[]")
```

### New method: `search_by_summary_depth(query, summary_depth, category, limit, verified_only)`

Controls which content is searched:
- **Depth 1**: Search `summary_short` only (fastest, high-level scan)
- **Depth 2**: Search `summary_short` + `summary_points`
- **Depth 3**: Search `summary_short` + `summary` + `content` (full search)

Implementation: Retrieve documents, construct tier-appropriate content string, score by keyword matching, return ranked results.

---

## 5. Indexer Changes

**File**: `src/rag_mcp/indexer.py`

### `_index_file()` modification

Extract summaries from `parsed.meta` and pass to `fts.upsert_document()`:

```python
summary_short=parsed.meta.summary_short,
summary_points=parsed.meta.summary_points,
summary=parsed.meta.summary,
highlights=parsed.meta.highlights,
```

### `update_document()` modification

Preserve existing summaries when updating content. Re-parse after write to pick up any frontmatter changes.

---

## 6. Vector Store Enhancement

**File**: `src/rag_mcp/vector_store.py`

### Summary-enriched embeddings

When upserting chunks, prepend `summary_short` + `summary_points` as context before the chunk content. This gives semantic search awareness of summary-level concepts without creating separate embeddings per tier.

```python
def upsert_chunks(self, category, chunks, document_summaries=None):
    # For each chunk, prepend summary context from document_summaries dict
    context_text = " | ".join([doc_summary["summary_short"]] + doc_summary["summary_points"])
    enriched_text = (context_text + " " + chunk["content"]).strip()
    # Embed enriched_text, but store original content
```

---

## 7. MCP Server Tools

**File**: `src/rag_mcp/server.py`

### New tool: `get_document_summary(file_path, tier=3) -> str`

Returns summary at requested abstraction level:
- Tier 1: One-liner only
- Tier 2: One-liner + bullet points
- Tier 3: One-liner + bullets + paragraph

Includes highlights if present. Useful when you need a quick overview without the full document.

### Enhanced `search()` tool

Add `summary_depth: int = 3` parameter. Delegates to `search_by_summary_depth()`. Tier 1 is for quick scans ("what repos do we have?"), Tier 3 for deep research.

### Enhanced `get_document()` tool

Include summaries and highlights in output, displayed before the main content.

---

## 8. Web UI

**File**: `src/rag_mcp/web/templates/document.html`

### Summaries section

Display above the main content in a collapsible/styled card:
- **One-Liner** — single line, bold
- **Key Points** — bullet list
- **Full Summary** — paragraph

Only show sections that are populated (all fields optional).

### Highlights display

Show highlighted terms as styled tags/chips below the summaries section. In the rendered content, `==text==` renders as `<mark>text</mark>` with yellow background.

### CSS

```css
.summary-tier__label { font-size: 0.75rem; text-transform: uppercase; color: var(--text-secondary); }
.summary-tier__content { font-size: 0.9rem; line-height: 1.5; }
.highlights-list__item { padding: 0.35rem 0.7rem; background: rgba(251, 191, 36, 0.15); border-radius: 4px; }
mark { background: rgba(251, 191, 36, 0.4); padding: 0.1em 0.2em; border-radius: 0.15em; }
```

**File**: `src/rag_mcp/web/app.py`

### page_document() modification

Pass `summary_short`, `summary_points`, `summary`, `highlights` to template context.

### `_highlight_rendered_content(html, highlights) -> str`

Post-process rendered HTML to wrap highlight terms in `<mark>` tags.

### api_search() modification

Accept `summary_depth` query parameter, delegate to `search_by_summary_depth()`.

---

## 9. Test Strategy

### Parser tests
- `test_extract_single_highlight` — `"==key concept=="` → `["key concept"]`
- `test_extract_multiple_highlights` — Multiple, deduplicated
- `test_parse_summary_points_yaml_list` — `[a, b, c]` format
- `test_parse_summary_points_comma_separated` — `"a, b, c"` format
- `test_parse_markdown_with_all_summary_fields` — Full frontmatter round-trip
- `test_parse_markdown_missing_summaries` — Fields default to empty

### FTS store tests
- `test_upsert_with_summaries` — Store and retrieve summary fields
- `test_search_by_summary_depth_tier_1` — Tier 1 only matches `summary_short`
- `test_search_by_summary_depth_tier_3` — Tier 3 matches full content
- `test_summary_json_roundtrip` — `summary_points` and `highlights` survive store/retrieve

### Integration tests
- `test_indexer_passes_summaries` — Full pipeline from markdown to FTS with summaries
- `test_vector_enrichment` — Chunks enriched with summary context

---

## Implementation Sequence

1. Parser: Add fields to `DocumentMeta`, implement highlight/summary extraction
2. Database: Add columns with migration, update upsert/retrieval
3. FTS Store: Implement `search_by_summary_depth()`
4. Indexer: Wire summaries through the pipeline
5. Vector Store: Add summary-enriched embeddings
6. MCP Tools: Add `get_document_summary()`, update `search()` and `get_document()`
7. Web UI: Render summaries, highlights, and `<mark>` tags
8. Tests: Full test coverage
