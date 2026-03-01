# Refactoring Plan: Full Decoupling of Semantic Search and Indexing (No Compatibility Proxies)

## 1. Problem Statement
Current `src/zotero_mcp/semantic_search.py` mixes two independent concerns:
1. Search/query path (retrieval, reranking, formatting)
2. Indexing/sync path (item loading, ingest orchestration, update schedule state)

This causes tight coupling and hidden cross-dependencies:
- Retrievers currently call `engine._legacy_*` and `engine._get_items_from_source` directly.
- Multiple entry points (MCP tools, server lifecycle, CLI) invoke update/status methods from `ZoteroSemanticSearch`.

Goal: **complete separation** of indexing and search responsibilities, with **no thin compatibility proxies kept in `ZoteroSemanticSearch`**.

## 2. Target Architecture

### New Components
- `src/zotero_mcp/indexer.py`
  - `ZoteroIndexer`: owns update configuration, should-update policy, item loading, and ingest execution.
- `src/zotero_mcp/searcher.py` (or keep filename `semantic_search.py` but class-only-search)
  - `ZoteroSearcher`: owns query-time behavior only.

### Revised Responsibilities
| Module | Responsibility |
| :--- | :--- |
| `chroma_client.py` | Low-level vector DB access (unchanged). |
| `indexer.py` | Update config read/write, should-update logic, ingestion orchestration, item source loading. |
| `searcher.py`/`semantic_search.py` | Query + rerank + result hydration/status (read-only DB info). |
| `retrievers/` | Retrieval strategy implementation; no direct dependency on legacy private methods in searcher. |

## 3. Non-Goals
- No backward-compat method proxies on `ZoteroSemanticSearch` (e.g. no retained `update_database()` wrapper).
- No incremental half-migration where old and new update paths coexist.

## 4. Breaking API Decision (Explicit)
The following methods are removed from `ZoteroSemanticSearch`:
- `should_update_database()`
- `update_database()`
- `_legacy_update_database()`
- `_get_items_from_source()` and related ingest-only helpers

All callers must switch to `ZoteroIndexer` in the same refactor.

### Public Contract Change List
- `create_semantic_search(...)` remains search-only and must not expose ingest/update operations.
- New factory `create_indexer(config_path: str | None = None, db_path: str | None = None)` is introduced for update/sync operations.
- Call sites that previously used `search.update_database(...)` / `search.should_update_database()` / `search.get_database_status()` for update state must migrate to indexer APIs.
- CLI and MCP tool behavior remains functionally equivalent for end users, but implementation binding changes from searcher methods to indexer methods.
- This is a single-release breaking change; no runtime compatibility shim.

## 5. Construction and Dependency Injection

### Factory Functions
1. Keep `create_semantic_search(...)` in search module (or move to `searcher.py`) returning `ZoteroSearcher`.
2. Add `create_indexer(...)` in `indexer.py` returning `ZoteroIndexer`.
3. Add a shared config loader (`load_semantic_config(...)`) that returns immutable typed config objects; factories must consume these objects instead of exposing raw `engine.semantic_config`.
4. Factories construct retrievers with explicit constructor arguments (config slices + service handles), not `engine` object references.

### Dependency Rules
1. `ZoteroSearcher` depends on query-time services only (`zotero_client`, retriever search strategy, optional reranker, read-only chroma access).
2. `ZoteroIndexer` depends on ingest-time services only (item source readers, update config persistence, retriever ingest strategy, chroma write path).
3. Retrievers receive explicit context objects/interfaces for search or ingest; they must not reach private methods or fields on another top-level component.
4. Shared utilities are moved into neutral helpers (e.g., item conversion helpers) instead of living on searcher/indexer classes.

### AdvancedRAG Config Injection (Critical)
1. `AdvancedRAGRetriever` must stop reading `engine.semantic_config` directly.
2. Constructor signature is changed to accept typed config slices explicitly:
   - `advanced_rag_chunk_config`
   - `advanced_rag_ingest_config`
   - `advanced_rag_retrieve_config`
   - `advanced_rag_reranker_config`
3. Ownership rule:
   - ingest-time chunk/ingest config is injected by `ZoteroIndexer` when building ingest retriever.
   - retrieve/reranker config is injected by `ZoteroSearcher` when building search retriever.
4. If one class instance still implements both ingest/search for `advanced_rag`, it must receive all required config slices from factories, never from `engine` fields.

## 6. Configuration Ownership and Boundaries

### Indexer-owned Config
- `semantic_search.update_config` subtree and all persisted update schedule fields (`auto_update`, `update_frequency`, `last_update`, etc.).
- Ingest-only extraction/source settings (`zotero_db_path`, extraction limits, batch ingest controls).
- `advanced_rag.chunk` and `advanced_rag.ingest` effective runtime values.

### Searcher-owned Config
- query-time retrieval/reranker config (`retriever_mode`, `advanced_rag.retrieve`, `advanced_rag.reranker`).
- display-time or search-result shaping options where applicable.

### Shared Config (Read-only by Both)
- Collection/model selection needed by both search and ingest (`chroma` location/model identity).
- Shared `advanced_rag` chunk schema definitions if used by both ingest and search; write responsibility remains with config authoring/setup flows, not runtime searcher.

### Boundary Enforcement
- Searcher must not write update schedule state.
- Indexer must not implement query result formatting or search enrichment logic.
- Any overlap is extracted to pure helpers/modules with no side effects.

## 7. Implementation Phases

### Phase 1: Introduce `ZoteroIndexer`
1. Create `src/zotero_mcp/indexer.py` with:
   - `_load_update_config()`
   - `_save_update_config()`
   - `should_update_database()`
   - `update_database(force_full_rebuild, limit, extract_fulltext)`
   - item sourcing pipeline previously used by legacy/advanced ingest.
2. Move ingest-only helper methods from `semantic_search.py` into `indexer.py`:
   - `_get_items_from_source()`
   - `_get_items_from_local_db()`
   - `_get_items_from_api()`
   - `_parse_creators_string()`
   - `_create_document_text()` / `_create_metadata()` / `_process_item_batch()` (if still needed by legacy retrievers)
3. `ZoteroIndexer` owns retriever ingest lifecycle (`retriever.ingest_data(...)`) and writes `last_update`.
4. Split `_get_items_from_local_db()` into smaller units before moving:
   - config resolution (`resolve_local_db_config`)
   - dedup selection (`dedupe_preprint_vs_journal`)
   - extraction decision (`should_extract_fulltext_for_item`)
   - item conversion (`local_item_to_api_item`)
5. Replace direct ad-hoc existence probing with an explicit strategy interface:
   - `EmbeddingExistenceChecker` (default Chroma-backed implementation)
   - indexer calls checker methods; local DB loader does not couple to raw Chroma APIs.

### Phase 2: Refactor Retriever Contracts
1. Remove retriever dependency on `engine._legacy_*` private methods.
2. Define explicit ingest/search dependencies:
   - Search retrievers depend on `ZoteroSearcher` (search-only context).
   - Ingest retrievers depend on `ZoteroIndexer` (ingest context).
3. Update `retrievers/legacy_metadata.py` and `retrievers/legacy_fulltext.py` to call indexer-owned ingest helpers directly.
4. Update `retrievers/advanced_rag.py` to consume item-loading helpers from indexer rather than searcher internals.
5. Remove `engine.semantic_config` access from retrievers; pass typed config slices via constructor/factory.

### Phase 3: Reduce `ZoteroSemanticSearch` to Search-only
1. Keep only search responsibilities:
   - config needed for retrieval/rerank
   - `search(...)`
   - read-only DB status for search concerns
2. Remove all update/sync methods and ingest helpers.
3. Keep `create_semantic_search(...)` returning searcher only.

### Phase 4: Migrate All Integration Points (Atomic)
Update all callers in one change set:
1. `src/zotero_mcp/server_tools/semantic_tools.py`
   - `update_search_database` -> instantiate/use `ZoteroIndexer`
   - `get_search_database_status` -> compose output from indexer + searcher as needed
2. `src/zotero_mcp/server_core.py`
   - startup auto-update uses `ZoteroIndexer.should_update_database()` + `ZoteroIndexer.update_database()`
3. `src/zotero_mcp/cli.py`
   - `update-db`, `db-status`, and info/status paths switched to indexer/searcher split
4. Any other direct update/status callers migrated (grep-driven verification).

### Phase 5: Cleanup
1. Delete dead code paths in `semantic_search.py` and retrievers.
2. Remove now-obsolete legacy private methods.
3. Consolidate duplicated `suppress_stdout` into shared utility module (single implementation reused by search/index/retrievers).
4. Optional rename: `updater.py` -> `pkg_updater.py` (separate concern, can be a follow-up PR).

## 8. Verification Plan

### Test Scope (must pass)
1. `tests/test_server_semantic_search.py`
2. `tests/test_retriever_delegation.py`
3. `tests/test_semantic_stats.py`
4. `tests/test_advanced_rag_retriever.py` (targeted relevant cases)

### New/Updated Tests Required
1. Indexer unit tests:
   - update config load/save
   - should-update frequency logic (`startup`, `daily`, `every_n`)
   - update flow sets `last_update`
   - `EmbeddingExistenceChecker` behavior and fallback handling
   - dedup and extraction-decision helpers
2. Integration tests:
   - MCP tool `update_search_database` path uses indexer
   - server startup auto-update path uses indexer
   - CLI `update-db` and `db-status` still behave correctly
3. Regression tests:
   - ensure search path does not require indexing components at runtime
   - ensure advanced_rag ingest still works with local DB/API fallback
   - ensure retrievers do not access `engine.semantic_config` or `engine._legacy_*`

### Manual Checks
1. Run `update_search_database` tool end-to-end.
2. Start server and confirm background auto-update behavior.
3. Run CLI `update-db` and `db-status` in both configured and non-configured scenarios.

## 9. Rollout Strategy
- Keep runtime behavior single-path in final merge (no compatibility proxy), but execute implementation in gated internal milestones:
1. Milestone A: introduce indexer + typed config loaders + helper extraction with no caller switch yet.
2. Milestone B: migrate retriever constructors/contracts and remove `engine.semantic_config` coupling.
3. Milestone C: switch all integration callers (`semantic_tools`/`server_core`/`cli`) in one commit window.
4. Milestone D: delete old methods and finalize cleanup.
- Enforce merge gates after each milestone: static checks, targeted tests, then full test suite.
- Block final merge unless all integration points compile and tests above pass.
