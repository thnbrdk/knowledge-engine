# Feature Plan: Contextual Retrieval (Session-Aware Search)

**Priority**: Medium  
**Depends On**: None (standalone, but complements Usage Analytics #5)  
**Foundation For**: Improved search relevance, personalized discovery  
**Theory**: Contextual memory — recent activity implicitly signals what the user cares about

---

## Overview

Add lightweight, privacy-first session tracking to the web UI. Track recently viewed documents, recent searches, and category engagement in memory. Use this context to boost search results toward categories the user has been exploring. Expose a "Recently Viewed" sidebar and "Recent Searches" section. MCP clients can pass context for the same boost.

---

## 1. Session Storage Architecture

### In-Memory Design (No Database)

Sessions are ephemeral — stored in a Python `dict`, never written to SQLite. This preserves privacy and avoids schema changes.

```python
@dataclass
class SessionData:
    session_id: str
    created_at: float          # time.time()
    last_activity: float
    recent_docs: deque          # maxlen=10, file_path LIFO
    recent_searches: deque      # maxlen=5, query LIFO
    recent_categories: dict     # {category: access_count}
```

### Session Lifecycle

1. **First request** → Generate UUID, set HttpOnly cookie, create `SessionData`
2. **Document view** → Append `file_path` to `recent_docs`, increment `recent_categories[category]`
3. **Search** → Append query to `recent_searches`
4. **24h inactivity** → Session evicted from memory on next cleanup cycle
5. **Stale cookie** → New session created (old UUID discarded)

### Cleanup

Background cleanup every 5 minutes: evict sessions where `now - last_activity > timeout`. No separate thread — triggered on each incoming request.

---

## 2. New File: Session Middleware — `src/rag_mcp/web/session_middleware.py`

### `SessionStore`

```python
class SessionStore:
    def __init__(self, session_timeout_minutes: int = 1440):
        self.sessions: dict[str, SessionData] = {}
        self.timeout_minutes = session_timeout_minutes

    def get_or_create(self, session_id: str | None) -> SessionData
    def track_document(self, session_id: str, file_path: str) -> None
    def track_search(self, session_id: str, query: str) -> None
    def track_category(self, session_id: str, category: str | None) -> None
    def get_session_data(self, session_id: str) -> SessionData | None
    def _cleanup_expired(self, now: float) -> None
```

### `SessionMiddleware` (Starlette `BaseHTTPMiddleware`)

- Reads `session_id` cookie from request
- Calls `session_store.get_or_create(session_id)`
- Attaches session to `request.state.session`
- Sets `session_id` cookie on response: `HttpOnly`, `Secure`, `SameSite=Lax`, `max_age=24h`

---

## 3. Configuration — `config.py`

### New `WebConfig` Fields

```python
@dataclass
class WebConfig:
    # ... existing fields ...
    session_timeout_minutes: int = 1440      # 24 hours
    max_recent_docs: int = 10
    max_recent_searches: int = 5
    search_boost_weight: float = 1.5         # Category boost multiplier
    track_sessions: bool = True              # Master on/off switch
    session_cookie_secure: bool = True       # Require HTTPS
```

### YAML Example

```yaml
web:
  session_timeout_minutes: 1440
  max_recent_docs: 10
  max_recent_searches: 5
  search_boost_weight: 1.5
  track_sessions: true
  session_cookie_secure: true
```

When `track_sessions: false`, middleware is not mounted and no cookies are set.

---

## 4. FTS Store Enhancement — `fts_store.py`

### Extended `search()` Signature

```python
def search(
    self,
    query: str,
    category: str | None = None,
    limit: int = 10,
    latest_approved: bool = False,
    boost_recent: bool = False,
    recent_categories: dict[str, int] | None = None,
    boost_weight: float = 1.5,
) -> list[FTSResult]:
```

### Boost Logic

After executing baseline FTS5 search:

1. If `boost_recent=False` or `recent_categories` is empty → return as-is
2. Compute `max_count = max(recent_categories.values())`
3. For each result, compute: `boost_factor = 1 + boost_weight * (category_count / max_count)`
4. Multiply BM25 rank by `boost_factor` (BM25 rank is negative, so multiplication preserves ordering direction)
5. Re-sort by adjusted rank

This promotes results in categories the user has been exploring, without filtering anything out.

---

## 5. Web App Integration — `app.py`

### Middleware Setup in `create_web_app()`

```python
from .session_middleware import SessionMiddleware, SessionStore

_session_store = SessionStore(config.web.session_timeout_minutes)

middleware = [
    Middleware(SessionMiddleware, session_store=_session_store),
]
```

Only added when `config.web.track_sessions` is `True`.

### Document Page — `page_document()`

- Get `session` from `request.state`
- Call `session_store.track_document(session.session_id, file_path)`
- Call `session_store.track_category(session.session_id, doc["category"])`
- Pass `recent_documents` (with titles) to template

### Search Page — `page_search()`

- Call `session_store.track_search(session.session_id, query)`
- Pass `recent_categories` to `fts.search()` with `boost_recent=True`
- Pass `recent_searches` list to template

### API Search — `api_search()`

- Same tracking and boost application as page search

---

## 6. Web UI Changes

### Document Template — "Recently Viewed" Sidebar

```html
{% if recent_documents %}
<div class="sidebar-section">
    <h3 class="sidebar-section__title">recently viewed</h3>
    <ul class="recent-list">
        {% for doc in recent_documents %}
        <li class="recent-list__item">
            <a href="/document/{{ doc.file_path }}">{{ doc.title }}</a>
        </li>
        {% endfor %}
    </ul>
</div>
{% endif %}
```

### Search Template — "Recent Searches" + Boost Indicator

```html
{% if recent.searches %}
<div class="search-history">
    <h3>recent searches</h3>
    <ul>
        {% for q in recent.searches %}
        <li><a href="/search?q={{ q|urlencode }}">{{ q }}</a></li>
        {% endfor %}
    </ul>
</div>
{% endif %}
```

Search results show a `★` badge when the result's category matches the user's recent activity.

### CSS

- `.recent-list` — clean list, no bullets, accent-colored links
- `.badge--boosted` — star indicator for category-boosted results
- `.document-sidebar--recent` — accent-colored top border

---

## 7. MCP Server Enhancement — `server.py`

### Extended `search()` Tool

```python
@mcp.tool()
async def search(
    query: str,
    category: str | None = None,
    search_type: str = "hybrid",
    context: dict | None = None,   # NEW
) -> str:
```

When `context` is provided with `recent_docs`, derive `recent_categories` from those file paths and apply the same boost logic. This allows MCP clients (e.g., Claude Desktop) to pass conversation context for better results.

**Client usage**:
```python
await search(
    query="Python decorators",
    context={"recent_docs": ["python/basics.md", "python/advanced.md"]}
)
```

---

## 8. Privacy Design

| Aspect | Decision |
|--------|----------|
| Storage | In-memory only — no database persistence |
| Cookie | HttpOnly, Secure, SameSite=Lax, 24h expiry |
| Data collected | File paths, search queries, category counts |
| Data NOT collected | IP addresses, user agents, PII |
| Retention | 24h inactivity → auto-eviction |
| Opt-out | `web.track_sessions: false` in config |
| GDPR | No personal data stored; sessions are anonymous UUIDs |

---

## 9. Test Strategy

### Session Store Unit Tests — `tests/test_session_management.py`

- `test_create_new_session` — UUID generated, empty deques
- `test_retrieve_existing_session` — Same session returned for same ID
- `test_track_document` — File paths added to `recent_docs`
- `test_recent_docs_maxlen` — Capped at 10 entries
- `test_track_search` — Queries added to `recent_searches`
- `test_track_category` — Category counts increment correctly
- `test_cleanup_expired_sessions` — Old sessions evicted

### FTS Boost Tests — `tests/test_fts_boost.py`

- `test_search_with_category_boost` — Python docs rank higher when Python category has activity
- `test_search_without_recent_categories` — Normal behavior when no context provided
- `test_boost_weight_configurable` — Different weights produce different rankings

### Web Integration Tests — `tests/test_web_session.py`

- `test_session_cookie_created` — Cookie present in response
- `test_session_persists_across_requests` — Same session ID reused
- `test_document_view_tracked` — Session's `recent_docs` updated after page load

---

## Implementation Sequence

1. Create `session_middleware.py` with `SessionStore`, `SessionData`, `SessionMiddleware`
2. Add config fields to `WebConfig` (session timeout, boost weight, track flag)
3. Add `boost_recent` / `recent_categories` params to `fts_store.search()`
4. Integrate middleware into `create_web_app()` (conditionally based on config)
5. Wire tracking into `page_document()`, `page_search()`, `api_search()`
6. Add "Recently Viewed" sidebar to `document.html`
7. Add "Recent Searches" section and boost badge to `search.html`
8. Extend MCP `search()` tool with `context` parameter
9. Write tests across all layers

---

## Files to Create

| File | Purpose |
|------|---------|
| `src/rag_mcp/web/session_middleware.py` | SessionStore, SessionData, SessionMiddleware |
| `tests/test_session_management.py` | Session store unit tests |
| `tests/test_fts_boost.py` | Category boost tests |
| `tests/test_web_session.py` | Web integration tests |

## Files to Modify

| File | Changes |
|------|---------|
| `src/rag_mcp/config.py` | Add 6 session-related WebConfig fields |
| `src/rag_mcp/fts_store.py` | Add boost params to `search()` |
| `src/rag_mcp/web/app.py` | Mount middleware, track in handlers |
| `src/rag_mcp/server.py` | Add `context` param to `search()` tool |
| `src/rag_mcp/web/templates/document.html` | "Recently Viewed" sidebar |
| `src/rag_mcp/web/templates/search.html` | "Recent Searches" + boost badge |

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| In-memory only | Privacy-first, no persistent tracking, simple cleanup |
| Category boost (not full personalization) | Effective, lightweight, no user profiling |
| HttpOnly + Secure cookies | Standard XSS protection |
| Master switch (`track_sessions`) | Easy opt-out for privacy-conscious deployments |
| Cleanup on request (not background thread) | No async complexity, sufficient for single-machine use |
| MCP context parameter | Allows programmatic clients to benefit from the same boost |
