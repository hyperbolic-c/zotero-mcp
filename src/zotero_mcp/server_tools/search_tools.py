"""Search tool registration and compatibility exports."""

from fastmcp import FastMCP

from zotero_mcp.server_tools.search_admin_tools import batch_update_tags
from zotero_mcp.server_tools.search_advanced_tools import advanced_search
from zotero_mcp.server_tools.search_basic_tools import get_recent, search_by_tag, search_items


def register(mcp: FastMCP) -> None:
    mcp.tool(name="zotero_search_items", description="Search for items in your Zotero library, given a query string.")(search_items)
    mcp.tool(name="zotero_search_by_tag", description="Search for items in your Zotero library by tag. Conditions are ANDed, each term supports disjunction (`OR`) and exclusion (`-`).")(search_by_tag)
    mcp.tool(name="zotero_get_recent", description="Get recently added items to your Zotero library.")(get_recent)
    mcp.tool(name="zotero_batch_update_tags", description="Batch update tags across multiple items matching a search query.")(batch_update_tags)
    mcp.tool(name="zotero_advanced_search", description="Perform an advanced search with multiple criteria.")(advanced_search)
