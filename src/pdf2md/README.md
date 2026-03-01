# pdf2md

将 Zotero 文献库中的 PDF 附件通过 [MinerU](https://github.com/opendatalab/MinerU) 解析为 Markdown 文件。

## 依赖

| 依赖 | 说明 |
|------|------|
| [Zotero 7](https://www.zotero.org/download/) | 必须在后台运行，并开启本地 API |
| [uv](https://docs.astral.sh/uv/) | Python 包管理工具 |
| MinerU Docker 服务 | 本地部署的 PDF 解析服务 |

### 开启 Zotero 本地 API

Zotero 7 → **编辑 → 首选项 → 高级** → 勾选
**"Allow other applications to communicate with Zotero"**

### 启动 MinerU Docker 服务

```bash
cd /path/to/MinerU/docker
docker compose --profile api up -d
```

默认监听 `http://localhost:8000`，可通过 `run.sh` 中的 `MINERU_API_URL` 修改。

---

## 安装

```bash
cd /path/to/pyzotero
uv sync --extra pdf2md
```

---

## 快速开始

### 方式一：启动脚本（推荐）

编辑 `src/pdf2md/run.sh`，填入你的配置：

```bash
MINERU_API_URL="http://localhost:8000"   # MinerU 服务地址
ZOTERO_LIBRARY_ID="YOUR_LIBRARY_ID"     # Zotero 用户库 ID
OUTPUT_DIR="./zotero_md_output"         # 输出目录
```

然后运行：

```bash
bash src/pdf2md/run.sh
```

脚本末尾的 `"$@"` 会将额外参数透传给 `pdf2md`，例如：

```bash
bash src/pdf2md/run.sh --skip-existing --limit 5 --verbose
```

### 方式二：直接调用

```bash
uv run pdf2md \
  --library-id YOUR_LIBRARY_ID \
  --api-url http://localhost:8000 \
  --output-dir ./zotero_md_output
```

---

## 输出结构

输出目录结构与 Zotero 本地存储保持一致：

```
zotero_md_output/
├── 9YVUCWAA/
│   └── Jamond 等 - 2016 - Piezo-generator integrating a vertical array of GaN nanowires.md
├── LYTPE6VE/
│   └── Zhang 等 - 2020 - High responsivity GaN nanowire UVA photodetector.md
└── ...
```

每个子目录名为 Zotero 附件的 item key，与 `~/Zotero/storage/` 下的目录名一一对应。

---

## 完整参数说明

| 参数 | 环境变量 | 默认值 | 说明 |
|------|----------|--------|------|
| `--library-id` | `ZOTERO_LIBRARY_ID` | 必填 | Zotero 用户库 ID |
| `--library-type` | — | `user` | `user` 或 `group` |
| `--collection` | — | 全库 | 仅处理指定文集（名称或 key） |
| `--api-url` | `MINERU_API_URL` | `http://localhost:8000` | MinerU 服务完整地址 |
| `--output-dir` | — | `./zotero_md_output` | Markdown 输出目录 |
| `--backend` | — | `pipeline` | 处理后端（默认 pipeline） |
| `--lang` | — | `auto` | 语言提示：`auto` / `ch` / `en` 等 |
| `--skip-existing` | — | 否 | 跳过已存在的 `.md` 文件，支持断点续跑 |
| `--limit N` | — | 无限制 | 限制处理的条目数量 |
| `-v / --verbose` | — | 否 | 输出调试日志 |

### 查找 Zotero 库 ID

Zotero 网页端：**https://www.zotero.org/settings/keys** → 页面顶部显示 `Your userID for use in API calls is XXXXXXX`

---

## 工作原理

```
pdf2md
  │
  ├─① pyzotero (local=True)
  │    └─ GET http://localhost:23119/api  ←─ Zotero 7 本地 API
  │         ├─ 获取所有条目列表
  │         ├─ 获取每个条目的子附件
  │         └─ 获取 PDF 文件字节（zot.file(key)）
  │
  └─② MinerU API
       └─ POST http://<MINERU_API_URL>/file_parse
            ├─ 上传 PDF 字节
            └─ 返回 Markdown 文本 → 写入输出目录
```

PDF 文件通过 Zotero 本地 API 读取，无需知道本地存储路径，也无需手动下载。

---

## 常见问题

**Q: 提示 "MinerU API is not reachable"**
确认 Docker 服务已启动：
```bash
docker ps | grep mineru-api
curl http://localhost:8000/docs
```

**Q: 提示 Zotero 连接失败**
确认 Zotero 7 正在运行，且已开启本地 API（端口 23119）：
```bash
curl http://localhost:23119/api
```

**Q: 部分 PDF 没有被处理**
添加 `--verbose` 查看详细日志，通常原因是该条目没有 PDF 附件，或附件为链接类型（非导入文件）。
