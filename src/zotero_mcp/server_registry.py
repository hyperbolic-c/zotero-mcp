"""Central tool registration for the Zotero MCP server."""

from fastmcp import FastMCP

from zotero_mcp.server_tools import (
    annotations_tools,
    connector_tools,
    item_tools,
    library_tools,
    notes_tools,
    search_tools,
    semantic_tools,
)


def register_all_tools(mcp: FastMCP) -> None:
    search_tools.register(mcp)
    item_tools.register(mcp)
    library_tools.register(mcp)
    notes_tools.register(mcp)
    annotations_tools.register(mcp)
    semantic_tools.register(mcp)
    connector_tools.register(mcp)
