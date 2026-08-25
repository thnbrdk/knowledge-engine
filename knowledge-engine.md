# Knowledge Engine

> If a project has no next action, it is stalled.

**Area:** Open
**Role:** Owner  
**Owner:** Thomas <!-- auto -->
**Status:** Parked
**Storage:** PATH_LOCAL
**Intake:**  <!-- auto -->
**Tags:** open

**Related:** [Technical Discussion Agent](technical-discussion-agent.md)

---

## Outcome

_MVP tested in a real scenario: the Technical Discussion Agent is embedded as a feature inside the Knowledge Engine, several real topics have been captured through discussion, and the results are searchable and retrievable through hybrid search. A parallel lightweight instruction-only version of the discussion agent exists for easy sharing and standalone use._

---

## Current Focus

_Integrate the Technical Discussion Agent as a feature inside the Knowledge Engine, then validate the full loop — discussion → capture → retrieval — with real topics._

---

## Next Actions

- [ ] Define the discussion agent mode/feature interface within the engine (how it captures, structures, and writes to the knowledge base)
- [ ] Build a parallel lightweight instruction-only version of the discussion agent for easy sharing and standalone use
- [ ] Run a real end-to-end pilot: discussion → capture → retrieval across multiple topics
- [ ] Validate that hybrid search returns useful results from discussion-generated content

---

## Open Questions

- How should the discussion agent trigger saves — automatic on conclusion, or user-initiated?
- What minimal frontmatter should the agent generate (categories, status, author)?
- Should the simple/parallel version share the same markdown format as the engine, or be more freeform?

## Resolved <!-- auto -->

- 

---

## Constraints

_What limits this project? Be honest._

- **Time:** Must fit alongside ongoing technical and operational responsibilities
- **Money:** No additional cost — all tools already available (Copilot, MCP, local Python stack)
- **Energy:** Requires consistent real-world usage to validate; the engine only proves itself through use

---

## Notes

### What's built

The core engine is complete and functional (`C:\projects\knowledge-engine`):

- **Indexer** — Incremental sync with change detection (mtime + content hash)
- **FTS Store** — SQLite FTS5 with BM25 ranking, revision history, status workflow
- **Vector Store** — LanceDB with sentence-transformer embeddings (384-dim), per-category tables
- **MCP Interface** — 7 tools (`search`, `get_document`, `get_related`, `list_categories`, `browse_category`, `save_knowledge`, `update_knowledge`), 2 resources, 2 prompts
- **Web UI** — 6 pages (index, category, document, search, admin, revision diffs), full REST API
- **Quality Gates** — draft → in_review → approved enforced at search layer; vectors only for approved content
- **Revision Tracking** — Append-only snapshots with `approved_by`/`in_review_by` accountability

### Roadmap (deferred until after MVP)

These features are designed and spec'd in `C:\projects\knowledge-engine\feature-plans\` but will not be built until the MVP is validated with real usage:

1. Wikilinks & Backlinks
2. Progressive Summarization (multi-tier summaries)
3. Staleness Metrics & Knowledge Decay
4. Review Tracking & Spaced Repetition
5. Usage Analytics
6. Explicit Relationship Types (knowledge graph)
7. Contextual Retrieval
8. Smart Capture with Context
9. Import Pipeline (URL, PDF, bulk markdown, code docstrings)
10. Multi-Reviewer Collaboration

### Companion project

The [Technical Discussion Agent](technical-discussion-agent.md) is the dynamic reasoning layer (Layer 1) that feeds into this engine (Layer 2). The agent handles fast, exploratory discussions; the engine captures and preserves the refined outcomes as durable, searchable knowledge.

### Design reference

Full design philosophy and architecture: `C:\projects\knowledge-engine\knowledge-engine-purpose.md`
