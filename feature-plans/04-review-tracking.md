# Feature Plan: Review Tracking & Spaced Repetition

**Priority**: High  
**Foundation For**: Spaced repetition, usage analytics, data-driven knowledge management  
**Theory**: Ebbinghaus Spacing Effect — retrieval practice is the strongest learning mechanism

---

## Overview

Track document access events (view, search click, export, edit, approve) to calculate freshness scores and surface documents due for re-review. The freshness score `1.0 / (1 + days_since_review / 30)` decays as time passes without review. An MCP tool and admin UI expose a "review queue" of stale documents.

---

## 1. Database Schema

**File**: `src/rag_mcp/fts_store.py`

### New table: `usage_events`

```sql
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,         -- 'view', 'search_click', 'export', 'edit', 'approve'
    timestamp TEXT NOT NULL,          -- ISO8601 UTC
    session_id TEXT NOT NULL,         -- UUID for session correlation
    metadata TEXT,                    -- JSON: {source, query, context}
    FOREIGN KEY (doc_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_usage_events_doc_timestamp
    ON usage_events(doc_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_usage_events_event_type
    ON usage_events(event_type, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_usage_events_timestamp_cleanup
    ON usage_events(timestamp);
```

### Denormalized columns on `documents`

```sql
ALTER TABLE documents ADD COLUMN last_reviewed_at TEXT NOT NULL DEFAULT '';
ALTER TABLE documents ADD COLUMN review_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE documents ADD COLUMN freshness_score REAL NOT NULL DEFAULT 1.0;
```

These are synced from `usage_events` on demand via `recalculate_review_stats()`.

---

## 2. FTS Store Methods

**File**: `src/rag_mcp/fts_store.py`

### Event logging

```python
def log_event(self, file_path: str, event_type: str, session_id: str | None = None,
              metadata: dict | None = None) -> bool:
```

Resolves `file_path` to `doc_id`, inserts into `usage_events`. Updates denormalized `last_reviewed_at`, `review_count`, `freshness_score` on the document row. Returns `True` if recorded, `False` if document not found.

Event types: `view`, `search_click`, `export`, `edit`, `approve`.

### Review statistics

```python
def get_review_stats(self, file_path: str) -> dict | None:
```

Returns: `{file_path, last_reviewed_at, review_count, days_since_review, freshness_score, event_counts: {view: n, ...}}`.

### Review queue

```python
def get_review_queue(self, days_threshold: int = 30, limit: int = 50) -> list[dict]:
```

Returns documents not reviewed in `> days_threshold` days (or never reviewed), sorted by `freshness_score ASC`. Each item includes title, category, freshness, days since review.

### Stale knowledge summary

```python
def get_stale_knowledge_summary(self) -> dict:
```

Returns aggregate stats: `{total_documents, stale_documents, very_stale_documents, never_reviewed, avg_freshness_score, by_category: {...}}`.

### Freshness-enhanced search

Add `boost_stale: bool = False` and `freshness_weight: float = 0.3` parameters to `search()`.

When `boost_stale=True`:
- Compute `combined_rank = fts_rank * (1 - weight) + freshness_score * weight`
- Invert freshness to surface under-reviewed docs: lower freshness → higher boost
- Re-sort results by combined rank

### Event cleanup

```python
def expire_old_events(self, days: int = 90) -> int:
```

Delete events older than N days. Recalculate denormalized stats afterward. Return count of deleted rows.

```python
def recalculate_review_stats(self) -> dict:
```

Sync `last_reviewed_at`, `review_count`, `freshness_score` from `usage_events` aggregation. Returns `{updated: n, scanned: n}`.

---

## 3. Freshness Formula

```
freshness_score = 1.0 / (1 + days_since_last_review / 30)
```

| Days Since Review | Score |
|-------------------|-------|
| 0 | 1.00 |
| 7 | 0.81 |
| 30 | 0.50 |
| 60 | 0.33 |
| 90 | 0.25 |
| 180 | 0.14 |
| Never reviewed | 0.00 |

---

## 4. Web App Integration

**File**: `src/rag_mcp/web/app.py`

### Event logging in existing routes

| Route | Event Type | Trigger |
|-------|-----------|---------|
| `page_document()` | `view` | GET request for document page |
| `page_search()` | `search_click` | User clicks a search result (via JS) |
| `api_document()` | `view` | API access to document |
| `api_document_content()` | `edit` | Content update POST |
| `api_document_metadata()` | `approve` | Status change to approved |
| `api_admin_export()` | `export` | Document exported |

### Session ID management

Use a cookie-based session ID (HTTP-only, auto-generated UUID). Middleware creates or reads the session cookie on each request.

### New endpoint: `POST /api/event-log`

For client-side event logging (e.g., search result click-through):

```python
async def api_event_log(request: Request) -> Response:
    body = await request.json()
    file_path = body.get("file_path")
    event_type = body.get("event_type")
    _fts.log_event(file_path, event_type, session_id=request.state.session_id)
    return JSONResponse({"ok": True})
```

---

## 5. MCP Server Tool

**File**: `src/rag_mcp/server.py`

### New tool: `get_review_queue(days_threshold=30, limit=20) -> str`

```python
@mcp.tool()
async def get_review_queue(days_threshold: int = 30, limit: int = 20) -> str:
    """Surface documents due for re-review based on last access.
    
    Returns documents not reviewed in > days_threshold days,
    sorted by freshness (stalest first). Useful for maintaining
    knowledge accuracy over time.
    """
```

Output format:
```
Review Queue (12 documents due):

1. **Kubernetes Upgrade Guide** [devops] — 0.14 freshness
   Last reviewed: 180 days ago
2. **OAuth2 Flow** [security] — 0.25 freshness  
   Last reviewed: 90 days ago
```

### Enhanced `search()` tool

Add `boost_stale: bool = False` parameter. When True, surfaces under-reviewed docs for re-verification.

---

## 6. Admin UI

**File**: `src/rag_mcp/web/templates/admin.html`

### Stale knowledge dashboard section

Stats cards: stale count, very stale count, never reviewed, average freshness.

Table of stale documents with freshness scores, sortable.

Controls: "Expire old events" and "Recalculate stats" admin actions.

### New API endpoints

- `GET /api/admin/stale-knowledge` — Returns stale knowledge summary
- `POST /api/admin/expire-events` — Trigger event cleanup
- `POST /api/admin/recalculate-stats` — Resync denormalized fields

---

## 7. Auto-Expiration

### Lazy expiration

On server startup (in lifespan), call `expire_old_events(days=90)` to clean events.

### Optional background task

Async cleanup every 24 hours:

```python
async def _cleanup_task():
    while True:
        await asyncio.sleep(86400)
        _fts.expire_old_events(days=90)
```

---

## 8. Privacy

- No PII stored — only file paths, event types, timestamps, session UUIDs
- Session IDs are random UUIDs, not correlated to user identity
- Events auto-expire after 90 days (configurable)
- No IP addresses or user agents logged

---

## 9. Test Strategy

### Event logging tests
- `test_log_event_creates_record` — Event stored correctly
- `test_log_event_missing_doc` — Returns False for nonexistent doc
- `test_log_event_validation` — Invalid event_type rejected

### Review stats tests
- `test_freshness_score_decay` — Score decreases over time
- `test_freshness_score_recent` — Just-reviewed → 1.0
- `test_freshness_score_never_reviewed` — No events → 0.0

### Review queue tests
- `test_review_queue_filtering` — Only stale docs returned
- `test_review_queue_ordering` — Stalest first
- `test_review_queue_threshold` — Respects days_threshold parameter

### Search boost tests
- `test_boost_stale_surfaces_underreviewed` — Stale docs ranked higher when boost_stale=True

### Cleanup tests
- `test_expire_old_events` — Events older than threshold deleted
- `test_recalculate_stats` — Denormalized fields synced correctly

### Integration tests
- `test_web_view_logs_event` — Visiting document page creates view event
- `test_backward_compatibility` — Works with no usage_events table initially

---

## Implementation Sequence

1. Database: Create `usage_events` table, add denormalized columns with migration
2. FTS Store: Implement `log_event()`, `get_review_stats()`, `get_review_queue()`
3. FTS Store: Implement `expire_old_events()`, `recalculate_review_stats()`
4. FTS Store: Add `boost_stale` to search
5. Web App: Wire event logging into existing routes
6. Server: Add `get_review_queue` MCP tool, enhance `search()`
7. Admin UI: Stale knowledge dashboard
8. Tests: Full test coverage
