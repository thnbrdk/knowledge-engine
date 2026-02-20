# Feature Plan: Usage Analytics

**Priority**: High  
**Depends On**: Review Tracking (feature #4) — reuses `usage_events` table  
**Foundation For**: Data-driven knowledge management, popularity-based ranking  
**Theory**: Evidence-based knowledge management — empirical data on what knowledge is actually valuable

---

## Overview

Build an analytics layer on top of the `usage_events` table (from Review Tracking). Add a search query log, an analytics dashboard, document popularity metrics, and CSV export. This answers: "What knowledge is popular? What's orphaned? What do people search for?"

---

## 1. Database Schema

**File**: `src/rag_mcp/fts_store.py`

### New table: `search_queries`

```sql
CREATE TABLE IF NOT EXISTS search_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    query_normalized TEXT NOT NULL,      -- lowercased, stripped
    timestamp TEXT NOT NULL,             -- ISO8601 UTC
    results_count INTEGER NOT NULL DEFAULT 0,
    click_doc_id INTEGER,               -- documents.id that was clicked (nullable)
    click_file_path TEXT,               -- file_path of clicked result
    session_id TEXT NOT NULL,
    execution_ms INTEGER DEFAULT 0,
    FOREIGN KEY (click_doc_id) REFERENCES documents(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_search_queries_normalized
    ON search_queries(query_normalized, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_search_queries_timestamp
    ON search_queries(timestamp DESC);
```

### Denormalized popularity columns on `documents`

```sql
ALTER TABLE documents ADD COLUMN view_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE documents ADD COLUMN export_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE documents ADD COLUMN last_viewed_at TEXT NOT NULL DEFAULT '';
ALTER TABLE documents ADD COLUMN popularity_score REAL NOT NULL DEFAULT 0.0;
```

`popularity_score` = normalized 0–1 score based on `view_count / max_view_count`.

---

## 2. FTS Store Methods

**File**: `src/rag_mcp/fts_store.py`

### Search logging

```python
def log_search_query(self, query: str, results_count: int, session_id: str,
                     execution_ms: int = 0) -> int:
```

Returns the `search_query_id` for later click attribution.

```python
def log_search_click(self, search_query_id: int, file_path: str) -> None:
```

Updates the `click_doc_id` / `click_file_path` on the search query record.

### Popularity metrics

```python
def get_document_popularity(self, file_path: str) -> dict:
```

Returns: `{view_count, export_count, click_count, last_viewed_at, popularity_score, unique_sessions}`.

```python
def recalculate_popularity_scores(self) -> dict:
```

Sync denormalized columns from `usage_events` and `search_queries`. Normalize `popularity_score` based on max view count. Returns `{updated: n}`.

### Analytics queries

```python
def get_analytics_top_documents(self, metric: str = "views", limit: int = 20) -> list[dict]:
```

Top documents by views, exports, or search clicks.

```python
def get_analytics_top_searches(self, limit: int = 20) -> list[dict]:
```

Top search queries with click-through rate (CTR) analysis.

```python
def get_analytics_discovery_patterns(self) -> dict:
```

Breakdown: how documents are found (search vs. browse vs. direct link).

### CSV export

```python
def export_analytics_csv(self, export_type: str) -> str:
```

Returns CSV string for `export_type` in `{documents, searches, patterns}`. Column definitions per type:

- **documents**: file_path, title, category, view_count, export_count, click_count, last_viewed_at, unique_sessions
- **searches**: query, search_count, clicks, ctr_percent, avg_results, last_searched
- **patterns**: discovery_type, count, sessions

---

## 3. Web App Routes

**File**: `src/rag_mcp/web/app.py`

### New routes

| Route | Method | Description |
|-------|--------|-------------|
| `/admin/analytics` | GET | Analytics dashboard page |
| `/api/admin/analytics` | GET | JSON API with `?endpoint=overview\|documents\|searches\|patterns` |
| `/api/admin/analytics/export` | GET | CSV download with `?type=documents\|searches\|patterns` |

### Search logging in existing routes

In `page_search()` and `api_search()`, log the query with results count and session ID. In the web UI, JavaScript logs click-through when a user clicks a search result.

### Event logging augmentation

Ensure `view` events update `view_count` and `last_viewed_at`. Ensure `export` events update `export_count`.

---

## 4. Analytics Dashboard

**File**: `src/rag_mcp/web/templates/analytics.html` (new template)

### Layout

- **Summary cards**: Total views, total searches, average CTR, most popular document
- **Top documents chart**: Bar chart (views, exports, clicks) with metric switcher
- **Top searches table**: Query, count, CTR %, clicked documents
- **Discovery patterns**: Breakdown showing search vs. browse vs. direct traffic
- **CSV export buttons**: One per data type

### Chart library

Use Chart.js (CDN) for bar charts and pie charts. Minimal footprint, no build step required.

---

## 5. MCP Server Enhancements

**File**: `src/rag_mcp/server.py`

### Enhanced `get_document()` tool

When `include_popularity=True`, append popularity metrics to the output: view count, export count, last viewed date.

### Enhanced `get_related()` tool

Add `popularity_weight: float = 0.0` parameter. When > 0:

```python
combined_score = (1 - popularity_weight) * similarity_score + popularity_weight * popularity_score
```

Re-rank results by combined score. This enables "what's popular AND similar" discovery.

---

## 6. Document Metadata Enrichment

In `get_document()` and `_normalize_document_dict()`, include popularity fields in the returned dict so the web UI can display view counts on document pages and category listings.

---

## 7. Test Strategy

### Search logging tests
- `test_log_search_query` — Record stored with correct fields
- `test_log_search_click` — Click attributed to correct query
- `test_search_normalized` — Query normalization (lowercase, strip)

### Popularity tests
- `test_view_count_incremented` — Multiple views increase count
- `test_popularity_score_normalized` — Max viewed doc = 1.0, others proportional
- `test_recalculate_popularity` — Sync from events matches expectations

### Analytics tests
- `test_top_documents_by_views` — Correct ordering
- `test_top_searches_with_ctr` — CTR calculated correctly
- `test_discovery_patterns` — Breakdown categories correct
- `test_csv_export_documents` — Valid CSV with expected columns

### Integration tests
- `test_analytics_dashboard_auth` — Admin token required
- `test_analytics_api_endpoints` — All endpoints return valid JSON
- `test_popularity_in_get_related` — Weight parameter affects ranking

---

## Implementation Sequence

1. Database: Create `search_queries` table, add popularity columns with migration
2. FTS Store: Implement `log_search_query()`, `log_search_click()`, popularity methods
3. FTS Store: Implement analytics query methods and CSV export
4. Web App: Wire search logging into existing routes
5. Web App: Create analytics dashboard route and template
6. Server: Add `popularity_weight` to `get_related()`, `include_popularity` to `get_document()`
7. Tests: Full test coverage

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Separate `search_queries` table | Permanent log (never expires) vs. transient `usage_events` |
| Denormalized popularity columns | Avoids expensive JOINs on every document retrieval |
| Normalized 0–1 popularity score | Consistent scale for blending with similarity scores |
| Chart.js via CDN | No build step, lightweight, sufficient for dashboard needs |
| Cookie-based session IDs | Correlate searches with clicks without requiring auth |
