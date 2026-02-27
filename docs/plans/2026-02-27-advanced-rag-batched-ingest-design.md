# Advanced RAG Batched Ingest Design

**Date:** 2026-02-27
**Branch:** feat/mineru-fulltext-rag
**Goal:** Reduce peak memory and CPU heat during vectorization by submitting chunks in batches with configurable sleep intervals.

## Problem

`AdvancedRAGRetriever.ingest_data()` currently accumulates all chunks from all items into three Python lists, then calls `chroma_client.upsert_documents()` once at the end. For large libraries (1000+ items), this causes:

1. **High memory peak**: All chunk text strings held in memory simultaneously.
2. **CPU burst**: ChromaDB's `all-MiniLM-L6-v2` embedding model runs inference on tens of thousands of chunks without pause, causing sustained high CPU load and hardware heat.
3. **No intermediate persistence**: A crash mid-run loses all progress.

The chunking logic itself (`_strip_images`, `_split_sections`, `_chunk_text`) is pure string manipulation and is not a bottleneck.

## Solution

Flush accumulated chunks to ChromaDB whenever the batch reaches a configurable size limit, then optionally sleep before continuing. A final flush handles any remaining chunks after the loop.

## Configuration

Add two fields under `semantic_search.advanced_rag.ingest` in `~/.config/zotero-mcp/config.json`:

```json
{
  "semantic_search": {
    "advanced_rag": {
      "ingest": {
        "batch_size": 500,
        "sleep_between_batches": 1.0
      }
    }
  }
}
```

- `batch_size` (int, default 500): Maximum number of chunks to accumulate before flushing to ChromaDB. Controls both memory usage and the size of each embedding inference burst.
- `sleep_between_batches` (float, default 1.0): Seconds to sleep between batch flushes. Set to 0 to disable. Gives the CPU breathing room between inference bursts.

## Implementation

### 1. Extract `_flush_batch` helper

Extract the upsert + stats-update logic into a private method:

```python
def _flush_batch(
    self,
    batch_docs: list[str],
    batch_metas: list[dict],
    batch_ids: list[str],
    stats: dict,
) -> None:
    existing_ids = self.chroma_client.get_existing_ids(batch_ids)
    self.chroma_client.upsert_documents(batch_docs, batch_metas, batch_ids)
    for doc_id in batch_ids:
        if doc_id in existing_ids:
            stats["updated_chunks"] += 1
        else:
            stats["added_chunks"] += 1
```

### 2. Modify `ingest_data` loop

Read `batch_size` and `sleep_between_batches` from config at the start of `ingest_data`. After extending the batch lists with each item's chunks, check if the batch has reached `batch_size`. If so, flush, clear the lists, and sleep.

```python
import time

batch_size = int(self.ingest_cfg.get("batch_size", 500))
sleep_seconds = float(self.ingest_cfg.get("sleep_between_batches", 1.0))

for item in items:
    # ... build docs/metas/ids for this item ...
    batch_docs.extend(docs)
    batch_metas.extend(metas)
    batch_ids.extend(ids)
    stats["processed_items"] += 1

    if len(batch_ids) >= batch_size:
        self._flush_batch(batch_docs, batch_metas, batch_ids, stats)
        batch_docs.clear()
        batch_metas.clear()
        batch_ids.clear()
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

# flush remaining
if batch_ids:
    self._flush_batch(batch_docs, batch_metas, batch_ids, stats)
```

## Files Changed

- `src/zotero_mcp/retrievers/advanced_rag.py`: Add `_flush_batch`, modify `ingest_data`

## Testing

- Existing unit tests must pass unchanged.
- Verify that `added_chunks` + `updated_chunks` totals match the non-batched run for the same input.
