#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# pdf2md 启动脚本
# 用法: bash src/pdf2md/run.sh
# ---------------------------------------------------------------------------
set -euo pipefail

# ── MinerU 服务地址 ──────────────────────────────────────────────────────────
MINERU_API_URL="http://192.168.1.176:8000"

# ── Zotero 配置 ──────────────────────────────────────────────────────────────
# Zotero 用户库 ID（在 Zotero → 设置 → Feeds/API 中查看）
ZOTERO_LIBRARY_ID="0"

# ── 输出目录 ─────────────────────────────────────────────────────────────────
OUTPUT_DIR="./zotero_md_output"

# ── 其他选项（按需取消注释） ──────────────────────────────────────────────────
# COLLECTION=""          # 仅处理指定文集（名称或 key）
# LIMIT=10               # 限制处理条目数量
BACKEND="pipeline"
LANG="en"            # 语言提示：auto / ch / en / ...
CONCURRENCY=1          # 并发数（单GPU建议为1）
TIMEOUT=600            # 超时时间（秒）

# ---------------------------------------------------------------------------
# 以下内容无需修改
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

ARGS=(
    --library-id "$ZOTERO_LIBRARY_ID"
    --api-url    "$MINERU_API_URL"
    --output-dir "$OUTPUT_DIR"
)

[ -n "${COLLECTION:-}"   ] && ARGS+=(--collection "$COLLECTION")
[ -n "${LIMIT:-}"       ] && ARGS+=(--limit "$LIMIT")
[ -n "${BACKEND:-}"     ] && ARGS+=(--backend "$BACKEND")
[ -n "${LANG:-}"        ] && ARGS+=(--lang "$LANG")
[ -n "${CONCURRENCY:-}" ] && ARGS+=(--concurrency "$CONCURRENCY")
[ -n "${TIMEOUT:-}"     ] && ARGS+=(--timeout "$TIMEOUT")
ARGS+=(--skip-existing)

echo "MinerU:  $MINERU_API_URL"
echo "Library: $ZOTERO_LIBRARY_ID"
echo "Output:  $OUTPUT_DIR"
echo ""

uv run pdf2md "${ARGS[@]}" "$@"
