# Feature Plan: Staleness Metrics & Knowledge Decay

**Priority**: High  
**Foundation For**: Review scheduling, confidence filtering, staleness dashboard  
**Theory**: Ebbinghaus Forgetting Curve — knowledge confidence degrades over time at measurable rates

---

## Overview

Knowledge is not static. Security docs from six months ago may be dangerously outdated, while mathematical proofs remain valid indefinitely. This feature adds confidence scoring based on exponential decay, configurable per document via a `decay_class` frontmatter field, with visual indicators in the web UI and filtering in search.

---

## 1. Parser Changes

**File**: `src/rag_mcp/markdown_parser.py`

### New field on `DocumentMeta`

```python
decay_class: str = "normal"  # fast | normal | slow
```

### Frontmatter extraction

```python
decay_class_value = str(fm.get("decay_class", "normal")).strip().lower()
if decay_class_value not in ("fast", "normal", "slow"):
    decay_class_value = "normal"
```

### Frontmatter example

```yaml
---
title: Kubernetes Security Best Practices
categories: [devops, security]
decay_class: fast
---
```

---

## 2. Database Schema

**File**: `src/rag_mcp/fts_store.py`

### New column on `documents` and `document_revisions`

```sql
decay_class TEXT NOT NULL DEFAULT 'normal'
```

### Migration

In `_migrate_schema()`, add column if missing via `ALTER TABLE ADD COLUMN`.

---

## 3. Confidence Calculation

Computed at **query time** (never stored — changes daily):

```python
import math
from datetime import datetime, timezone

HALF_LIVES = {"fast": 90, "normal": 180, "slow": 365}

def _compute_confidence(self, last_modified: float, decay_class: str = "normal") -> float:
    """Exponential decay: exp(-days_since_update / half_life). Returns 0.0–1.0."""
    half_life = HALF_LIVES.get(decay_class, 180)
    now = datetime.now(timezone.utc).timestamp()
    days = (now - last_modified) / 86400
    return max(0.0, min(1.0, math.exp(-days / half_life)))
```

### Decay class behavior

| Class | Half-Life | Use Case | 50% at | 30% at |
|-------|-----------|----------|--------|--------|
| `fast` | 90 days | Security, trending tech, version-specific | 3 months | 4 months |
| `normal` | 180 days | General engineering, patterns | 6 months | 8 months |
| `slow` | 365 days | Fundamentals, math, architecture principles | 1 year | 1.5 years |

### Badge thresholds

| Confidence | Badge | Color |
|------------|-------|-------|
| > 90% | Fresh | Green |
| 70–90% | Aging | Yellow |
| < 70% | Stale — verify | Red |

---

## 4. FTS Store Changes

**File**: `src/rag_mcp/fts_store.py`

### `upsert_document()` modification

Add `decay_class` parameter. Include in INSERT and UPDATE SQL.

### `_upsert_revision_snapshot()` modification

Pass `decay_class` to revision snapshots.

### `search()` enhancement — confidence filtering

Add `confidence_min: float | None = None` parameter. After FTS query, post-filter:

```python
if confidence_min is not None:
    filtered = []
    for r in results:
        doc = self.get_document(r.file_path)
        if doc:
            confidence = self._compute_confidence(doc["last_modified"], doc.get("decay_class", "normal"))
            if confidence >= confidence_min:
                filtered.append(r)
    results = filtered
```

### New method: `get_all_documents_with_confidence()`

Return all documents with computed confidence scores, for the admin staleness dashboard.

---

## 5. Server.py Changes

**File**: `src/rag_mcp/server.py`

### Enhanced `search()` tool

Add `confidence_min: float | None = None` parameter. Pass to FTS search. Include confidence score and badge in result formatting:

```python
if confidence > 0.90:
    badge = "🟢"
elif confidence > 0.70:
    badge = "🟡"
else:
    badge = "🔴 (stale)"

lines.append(f"{i}. **{title}** [{category}] {badge} — {confidence_pct}%")
```

For stale results, append warning: `"⚠️ Last updated X days ago — verify currency"`

---

## 6. Indexer Changes

**File**: `src/rag_mcp/indexer.py`

### `_index_file()` modification

Extract `decay_class` from `parsed.meta.decay_class`, pass to `fts.upsert_document()`.

### `add_document()` modification

Accept optional `decay_class` parameter, include in generated frontmatter.

---

## 7. Web UI

### Document viewer

**File**: `src/rag_mcp/web/templates/document.html`

Add confidence badge in the document header, next to the status dropdown:

```html
{% set confidence_pct = (doc._confidence * 100) | int %}
{% if doc._confidence >= 0.90 %}
    <span class="confidence-badge confidence-badge--fresh">{{ confidence_pct }}% fresh</span>
{% elif doc._confidence >= 0.70 %}
    <span class="confidence-badge confidence-badge--aging">{{ confidence_pct }}% current</span>
{% else %}
    <span class="confidence-badge confidence-badge--stale">{{ confidence_pct }}% — verify</span>
{% endif %}
```

Also show `decay_class` and "updated X days ago" in the metadata bar.

### CSS

```css
.confidence-badge { padding: 0.25rem 0.5rem; border-radius: 4px; font-size: 0.8rem; font-weight: 600; }
.confidence-badge--fresh { background: rgba(34, 197, 94, 0.2); color: #22c55e; border: 1px solid #22c55e; }
.confidence-badge--aging { background: rgba(234, 179, 8, 0.2); color: #eab308; border: 1px solid #eab308; }
.confidence-badge--stale { background: rgba(239, 68, 68, 0.2); color: #ef4444; border: 1px solid #ef4444; }
```

### Admin staleness dashboard

**File**: `src/rag_mcp/web/templates/admin.html`

New "staleness report" section with filter buttons (All / Fresh / Aging / Stale) and a table showing each document's title, category, decay_class, confidence %, days since update.

**File**: `src/rag_mcp/web/app.py`

New route: `GET /api/admin/staleness` — returns all documents with computed confidence scores, sorted stalest-first.

### page_document() modification

Compute `doc["_confidence"]` before passing to template:

```python
doc["_confidence"] = _fts._compute_confidence(doc["last_modified"], doc.get("decay_class", "normal"))
```

---

## 8. Configuration (Optional)

**File**: `src/rag_mcp/config.py`

Optional `DecayConfig` dataclass for customizable half-lives:

```yaml
decay:
  fast_half_life_days: 90
  normal_half_life_days: 180
  slow_half_life_days: 365
```

---

## 9. Test Strategy

### Confidence calculation tests
- `test_confidence_fresh` — 30 days / normal → ~0.846
- `test_confidence_stale` — 400 days / normal → ~0.118
- `test_fast_vs_normal` — At 180 days: normal ≈ 0.5, fast ≈ 0.25
- `test_slow_retention` — At 365 days: slow ≈ 0.5
- `test_clamped_to_0_1` — Edge cases: very old → 0.0, just updated → 1.0

### Frontmatter tests
- `test_decay_class_parsed` — `decay_class: fast` extracted correctly
- `test_decay_class_default` — Missing → `"normal"`
- `test_decay_class_invalid` — `decay_class: invalid` → `"normal"`

### Search filtering tests
- `test_confidence_min_filters` — Stale docs excluded when `confidence_min=0.70`
- `test_confidence_in_results` — Score included in formatted output

### Integration tests
- `test_staleness_dashboard_data` — Admin endpoint returns correct rank order
- `test_decay_class_through_pipeline` — Full flow: markdown → parse → index → retrieve

---

## Implementation Sequence

1. Parser: Add `decay_class` field, extract from frontmatter
2. Database: Add column with migration
3. FTS Store: Add `_compute_confidence()`, update `upsert_document()`, add `confidence_min` filtering
4. Indexer: Pass `decay_class` through pipeline
5. Server: Add `confidence_min` to search tool, format confidence in results
6. Web: Confidence badges, staleness dashboard, admin API
7. Tests: Full test coverage
