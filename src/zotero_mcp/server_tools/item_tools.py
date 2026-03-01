"""Item and collection tool registration and compatibility exports."""

from fastmcp import FastMCP

from zotero_mcp.server_tools.collection_tools import get_collection_items, get_collections
from zotero_mcp.server_tools.item_metadata_tools import (
    get_item_children,
    get_item_fulltext,
    get_item_metadata,
    get_tags,
)


def register(mcp: FastMCP) -> None:
    mcp.tool(name="zotero_get_item_metadata", description="Get detailed metadata for a specific Zotero item by key.")(get_item_metadata)
    mcp.tool(name="zotero_get_item_fulltext", description="Get the full text content of a Zotero item's attachment.")(get_item_fulltext)
    mcp.tool(name="zotero_get_collections", description="Get all collections in your Zotero library with optional nested structure.")(get_collections)
    mcp.tool(name="zotero_get_collection_items", description="Get items in a specific collection by collection key.")(get_collection_items)
    mcp.tool(name="zotero_get_item_children", description="Get child items (attachments, notes) for a specific Zotero item.")(get_item_children)
    mcp.tool(name="zotero_get_tags", description="Get all tags used in your Zotero library.")(get_tags)
