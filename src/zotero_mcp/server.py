"""Zotero MCP server entrypoint and backward-compatible exports."""

from zotero_mcp.server_core import create_mcp, server_lifespan
from zotero_mcp.server_registry import register_all_tools
from zotero_mcp.server_tools.annotations_tools import (
    create_annotation,
    get_annotations,
)
from zotero_mcp.server_tools.connector_tools import (
    chatgpt_connector_search,
    connector_fetch,
)
from zotero_mcp.server_tools.item_tools import (
    get_collection_items,
    get_collections,
    get_item_children,
    get_item_fulltext,
    get_item_metadata,
    get_tags,
)
from zotero_mcp.server_tools.library_tools import (
    get_feed_items,
    list_feeds,
    list_libraries,
    switch_library,
    validate_library_switch,
)
from zotero_mcp.server_tools.notes_tools import create_note, get_notes, search_notes
from zotero_mcp.server_tools.search_tools import (
    advanced_search,
    batch_update_tags,
    get_recent,
    search_by_tag,
    search_items,
)
from zotero_mcp.server_tools.semantic_tools import (
    get_search_database_status,
    semantic_search,
    update_search_database,
)


mcp = create_mcp()
register_all_tools(mcp)

__all__ = [
    "mcp",
    "server_lifespan",
]
