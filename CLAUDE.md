# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Zotero MCP is a Model Context Protocol (MCP) server that connects AI assistants to Zotero research libraries. It enables semantic/keyword search, metadata retrieval, full-text extraction, and annotation handling.

## Commands

```bash
# Development setup
pip install -e .                    # Install in dev mode
pip install pre-commit && pre-commit install  # Pre-commit hooks

# Linting
pre-commit run --all-files          # Run all hooks
black src/zotero_mcp/               # Format code
isort src/zotero_mcp/               # Sort imports

# Testing
pytest                              # Run tests

# Build & publish
python -m build                     # Build distribution
```

## Architecture

### Main Entry Points

- **CLI (`src/zotero_mcp/cli.py`)**: `zotero-mcp` command with subcommands:
  - `serve` - Run MCP server (default)
  - `setup` - Interactive Claude Desktop configuration
  - `update-db` - Rebuild semantic search index
  - `db-status` - Show index status
  - `update` - Update zotero-mcp to latest version

- **MCP Server (`src/zotero_mcp/server.py`)**: FastMCP-based server exposing 20+ tools for search, metadata, full-text, annotations, and library navigation.

### Key Modules

| Module | Purpose |
|--------|---------|
| `client.py` | Zotero API client wrapper |
| `local_db.py` | Direct SQLite access to `zotero.sqlite` |
| `semantic_search.py` | Vector search with ChromaDB |
| `chroma_client.py` | Embedding functions (local, OpenAI, Gemini) |
| `setup_helper.py` | Interactive CLI setup |
| `updater.py` | Smart update with config preservation |
| `pdfannots_*.py` | PDF annotation extraction |

### Data Sources

- **Local mode**: Direct SQLite access to Zotero's `zotero.sqlite`
- **Web API mode**: Remote Zotero API via `pyzotero`
- **Vector store**: ChromaDB for semantic search embeddings

### Transport Options

- `stdio` - Default for Claude Desktop
- `streamable-http` / `sse` - For web-based clients

## Configuration

- Runtime config: `~/.config/zotero-mcp/config.json`
- Claude Desktop config: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Environment variables: `ZOTERO_LOCAL`, `ZOTERO_API_KEY`, `ZOTERO_LIBRARY_ID`, `OPENAI_API_KEY`, `GEMINI_API_KEY`

## Code Style

- Line length: 88 characters (black)
- Python 3.10+ required
- Pre-commit hooks: `pyupgrade`, `trailing-whitespace`, TOML/YAML validation
