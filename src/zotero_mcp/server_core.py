"""Core MCP server lifecycle and construction."""

import asyncio
import sys
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastmcp import FastMCP


@asynccontextmanager
async def server_lifespan(server: FastMCP):
    """Manage server startup and shutdown lifecycle."""
    sys.stderr.write("Starting Zotero MCP server...\\n")
    background_task: asyncio.Task | None = None

    try:
        from zotero_mcp.semantic_search import create_semantic_search

        config_path = Path.home() / ".config" / "zotero-mcp" / "config.json"

        if config_path.exists():
            search = create_semantic_search(str(config_path))

            if search.should_update_database():
                sys.stderr.write("Auto-updating semantic search database...\\n")

                async def background_update():
                    try:
                        # Use indexer directly for auto-update
                        stats = await asyncio.to_thread(
                            search.indexer.update_database, extract_fulltext=False
                        )
                        sys.stderr.write(
                            f"Database update completed: {stats.get('processed_items', 0)} items processed\\n"
                        )
                    except Exception as e:
                        sys.stderr.write(f"Background database update failed: {e}\\n")

                background_task = asyncio.create_task(background_update())

    except Exception as e:
        sys.stderr.write(f"Warning: Could not check semantic search auto-update: {e}\\n")

    yield {}

    if background_task and not background_task.done():
        background_task.cancel()
        with suppress(asyncio.CancelledError):
            await background_task

    sys.stderr.write("Shutting down Zotero MCP server...\\n")


def create_mcp() -> FastMCP:
    """Create the FastMCP instance."""
    return FastMCP("Zotero", lifespan=server_lifespan)
