# Zotero MCP 向量化检索设计文档

本文档描述 Zotero MCP 的语义搜索（Semantic Search）架构，包括数据流向、存储结构和关键设计决策。

## 1. 概述

Zotero MCP 支持两种检索模式：

| 模式 | 说明 |
|------|------|
| `legacy_metadata` | 仅索引 Zotero 论文的元数据（标题、作者、摘要） |
| `legacy_fulltext` | 在 legacy_metadata 基础上增加 PDF 全文提取 |
| `advanced_rag` | **当前推荐模式**：基于 Markdown 文件的智能分块、参考文献分离、Rerank 排序 |

## 2. 架构组件

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Zotero MCP Server                           │
├─────────────────────────────────────────────────────────────────────┤
│  CLI / Tools                                                        │
│    ├── zotero-update-search-database                                │
│    ├── zotero-semantic-search                                       │
│    └── zotero-get-search-database-status                            │
├─────────────────────────────────────────────────────────────────────┤
│  Core Modules                                                       │
│    ├── indexer.py         → 索引入口，协调数据源与 retriever       │
│    ├── semantic_search.py → 搜索入口，协调 retriever 与结果处理    │
│    └── chroma_client.py   → ChromaDB 底层封装                      │
├─────────────────────────────────────────────────────────────────────┤
│  RAG Module (rag/)                                                  │
│    ├── advanced_rag.py   → Facade 入口，协调 Ingestor/Searcher     │
│    ├── ingestor.py       → 数据索引（从 SQLite 读取并写入 ChromaDB）│
│    ├── searcher.py       → 语义搜索、结果聚合、引用解析            │
│    ├── reranker.py       → FlashRank 重排序                       │
│    ├── chunkers.py       → 文档分块策略                           │
│    ├── reference_parser.py → 参考文献解析与分离                    │
│    ├── compat.py         → 测试用 monkeypatch 兼容层               │
│    └── utils.py          → 共享工具函数                           │
└─────────────────────────────────────────────────────────────────────┘
```

### Facade 模式

`AdvancedRAGRetriever` 作为 Facade，协调三个子模块：

```
AdvancedRAGRetriever (Facade)
    │
    ├── Ingestor (数据索引)
    │     ├── _fetch_items_and_attachments_from_local_db()
    │     ├── _build_item_chunks()
    │     ├── _flush_batch()
    │     └── ingest()
    │
    ├── Searcher (语义搜索)
    │     ├── _hydrate_items()
    │     ├── _resolve_citations()
    │     └── search()
    │
    └── Reranker (重排序)
          └── rerank()
```

**设计原则**：
- Facade 层只做委托，不承载业务逻辑
- 子模块各自独立，通过 Facade 组合
- 通过 `compat.py` 支持测试 monkeypatch

## 3. 数据流

### 3.1 索引（Ingest）

```
Zotero SQLite (本地模式)
         │
         ▼
LocalZoteroReader.get_items_with_text()
         │
         ▼
Ingestor._fetch_items_and_attachments_from_local_db()
         │
         ▼
对每个 item:
  1. 查找对应的 Markdown 文件 (md_root/{attachment_key}/*.md)
  2. 合并多个 md 文件内容
  3. strip_and_extract_references() 分离正文与参考文献
  4. ChunkingBackend.chunk() 分块
  5. 分别存入两个 ChromaDB 集合
```

**两个 ChromaDB 集合**：

| 集合名 | 存储内容 | ID 格式 |
|--------|----------|---------|
| `zotero_rag_chunks_v1` | 正文 chunks + 元数据 chunk | `{item_key}:{attachment_key}:{chunk_index}` |
| `zotero_rag_refs_v1` | 参考文献原文（纯文本，非向量） | `{item_key}:{attachment_key}:ref:{ref_num}` |

### 3.2 搜索（Search）

```
用户查询
   │
   ▼
Searcher.search()
   │
   ▼
ChromaDB ANN 搜索 (zotero_rag_chunks_v1)
   │
   ▼
候选结果按 item_key 聚合
   │
   ▼
FlashRank Rerank（可选）
   │
   ▼
从 zotero_rag_refs_v1 解析引用编号，获取参考文献原文
   │
   ▼
返回结果（含 resolved_citations）
```

### 3.3 性能优化

**本地模式 SQL 查询优化**：

- `_hydrate_items()` 使用 `LocalZoteroReader.get_items_by_keys()` 按 key 批量查询
- 使用参数化查询防止 SQL 注入
- 避免全表扫描：`get_items_with_text()` → `get_items_by_keys(item_keys)`

**ChromaDB 批处理**：

- `CHROMA_MAX_BATCH` 根据 SQLite `MAX_VARIABLE_NUMBER // 6` 动态计算
- 超过限制时自动分片写入

## 4. 关键模块

### 4.1 chunkers.py - 分块策略

支持两种分块后端：

```python
class ChunkingBackend(ABC):
    def chunk(self, merged_markdown_text: str) -> list[ChunkRecord]: ...
```

| 后端 | 策略 | 说明 |
|------|------|------|
| `LegacyChunkingBackend` | 原始滑动窗口 | 按 `#` 标题分割章节，再按固定长度滑动 |
| `LangChainMarkdownRecursiveChunker` | `markdown_recursive_v1` | MarkdownHeaderTextSplitter + RecursiveCharacterTextSplitter |

**默认配置**：

```python
chunk = {
    "backend": "langchain",
    "strategy": "markdown_recursive_v1",
    "chunk_size": 1100,
    "chunk_overlap": 180,
    "min_chunk_chars": 220,
    "separators": ["\n\n", "\n", ". ", "; ", ", ", " "],
    "headers": ["#", "##", "###", "####"],
    "exclude_sections_enabled": True,
    "exclude_sections": ["references", "acknowledgments", "appendix", "supplementary"],
    "detect_reference_block_without_heading": True,
    "reference_block_tail_ratio": 0.35,
    "reference_block_window_lines": 20,
    "reference_block_min_density": 0.45,
    "reference_block_min_hits": 8,
}
```

### 4.2 reference_parser.py - 参考文献处理

**两步走**：

1. **定位参考文献块**
   - Stage 1：精确标题匹配（`# References`、`# Bibliography`、`# 参考文献`）
   - Stage 2：无标题时自动检测——若文档末尾 35% 区域中，每 20 行滑窗内 ≥45% 是 `[1]`/`1.` 格式，且含 `doi:`、`et al.` 等 bib token，则识别为参考文献

2. **解析单条参考文献**
   ```python
   _REF_ITEM_RE = re.compile(r"^\s*(?:\[(\d+)\]|(\d+)\.)\s*(.+?)\s*$")
   ```
   输出 `ref_map = {1: "Author et al. ...", 2: "...", ...}`

**存储设计**：参考文献存入独立的 `zotero_rag_refs_v1` 集合，**使用全零向量**（`upsert_raw`），检索时通过精确 ID 查找，而非语义相似度搜索。这样避免参考文献污染主搜索结果。

### 4.3 advanced_rag.py - Facade 入口

```python
class AdvancedRAGRetriever(BaseRetriever):
    """Facade combining Ingestor, Searcher, and Reranker."""

    def ingest_data(force_rebuild=False, limit=None):
        # 委托给 Ingestor.ingest()
        return self._ingestor.ingest(...)

    def search(query, limit, filters, ...):
        # 委托给 Searcher.search()
        return self._searcher.search(...)

    # 为测试保留的后向兼容方法
    def _build_item_chunks(...): ...
    def _flush_batch(...): ...
```

### 4.4 ingestor.py - 数据索引

```python
class Ingestor:
    def ingest(force_rebuild=False, limit=None, build_chunks_fn=None):
        # 1. 从 SQLite 读取 items + attachments
        # 2. 对每个 item 构建 chunks
        # 3. 批量写入 ChromaDB

    def _fetch_items_and_attachments_from_local_db(limit): ...
    def _build_item_chunks(item, attachment_keys): ...
    def _flush_batch(...): ...          # 实际写入逻辑
    def flush_batch(...): ...           # 公开委托方法
    def flush_refs_batch(...): ...      # 参考文献写入
```

### 4.5 searcher.py - 语义搜索

```python
class Searcher:
    def search(query, limit, filters,
               include_citation_references=True,
               citation_max_items_per_result=8):
        # 1. ChromaDB ANN 搜索
        # 2. 按 item_key 聚合，meta 权重 0.7
        # 3. FlashRank Rerank（可选）
        # 4. 解析正文引用编号，查 refs 集合获取参考文献原文

    def _hydrate_items(item_keys): ...  # 从本地 DB 或 API 获取元数据
    def _resolve_citations(...): ...     # 解析引用编号
```

### 4.6 compat.py - 测试兼容层

提供可被 monkeypatch 的导入，支持测试注入：

```python
# compat.py
def __getattr__(name: str):
    if name == "is_local_mode":
        # 检查 advanced_rag 是否被 monkeypatch
        ...
        return func
    if name == "LocalZoteroReader":
        # 检查 advanced_rag 是否被 monkeypatch
        ...
        return cls
```

**使用方式**：

```python
# Ingestor / Searcher 中
from . import compat

if compat.is_local_mode():
    with compat.LocalZoteroReader(db_path=...) as reader:
        ...
```

测试时可以 monkeypatch `advanced_rag.is_local_mode` 和 `advanced_rag.LocalZoteroReader`。

## 5. ChromaDB 存储结构

### 5.1 文件布局

```
~/.config/zotero-mcp/chroma_db/
├── chroma.sqlite3           # 元数据、文档原文、collection 定义
├── {uuid}-.../              # HNSW 向量索引（每个 segment 一个文件夹）
│   ├── data_level0.bin      # 第 0 层向量数据 + 邻居链接
│   ├── header.bin           # 索引头部信息
│   ├── length.bin           # 每个节点的邻居数
│   └── link_lists.bin      # 上层邻居链接
```

### 5.2 Collection 与 Segment

| collection | segment type | 说明 |
|------------|-------------|------|
| `zotero_rag_chunks_v1` | VECTOR (HNSW) | 语义向量 |
| `zotero_rag_chunks_v1` | METADATA (SQLite) | metadata 索引 |
| `zotero_rag_refs_v1` | VECTOR (HNSW) | **全零向量**（无实际作用） |
| `zotero_rag_refs_v1` | METADATA (SQLite) | 引用原文存储 |

**HNSW 文件残余问题**：执行 `force_rebuild` 时，ChromaDB 会删除旧 collection 的 UUID 文件夹，但异常中断可能导致残余。可手动清理：

```bash
# 找出有效 UUID
sqlite3 ~/.config/zotero-mcp/chroma_db/chroma.sqlite3 \
  "SELECT id FROM segments WHERE type LIKE '%hnsw%';"

# 删除磁盘上无对应的文件夹
```

## 6. 配置

配置文件：`~/.config/zotero-mcp/config.json`

```json
{
  "semantic_search": {
    "retriever_mode": "advanced_rag",
    "md_root": "/path/to/mineru/output",  // MinerU 输出目录
    "advanced_rag": {
      "chunk": {
        "backend": "langchain",
        "strategy": "markdown_recursive_v1",
        "chunk_size": 1100,
        "chunk_overlap": 180
      },
      "reranker": {
        "enabled": true,
        "backend": "flashrank",
        "model_name": "ms-marco-MiniLM-L-12-v2",
        "top_n": 8
      },
      "retrieve": {
        "candidate_k": 30,
        "evidence_per_item": 2,
        "meta_weight": 0.70
      }
    }
  }
}
```

### 配置项说明

| 路径 | 默认值 | 说明 |
|------|--------|------|
| `retriever_mode` | `legacy_metadata` | 检索模式 |
| `md_root` | - | MinerU 输出的 Markdown 文件根目录 |
| `chunk.chunk_size` | 1100 | 每个 chunk 的最大字符数 |
| `chunk.chunk_overlap` | 180 | 相邻 chunk 的重叠字符数 |
| `chunk.exclude_sections` | `[references, ...]` | 排除的章节名 |
| `reranker.enabled` | true | 是否启用 Rerank |
| `retrieve.candidate_k` | 30 | ChromaDB 检索候选数 |
| `retrieve.meta_weight` | 0.70 | 元数据权重（0-1，1 只用元数据） |

## 7. 常用命令

```bash
# 更新语义搜索数据库
zotero-mcp update-db --rebuild

# 查看数据库状态
zotero-mcp db-status

# 语义搜索
zotero-semantic-search "machine learning transformers"
```

## 8. 扩展阅读

- **ChromaDB**: https://docs.trychroma.com/
- **LangChain Text Splitters**: https://python.langchain.com/docs/modules/data_connection/document_transformers/
- **FlashRank**: https://github.com/PrithivirajDamodaran/FlashRank
- **HNSW**: https://arxiv.org/abs/1603.09320
