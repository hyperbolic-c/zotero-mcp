"""Library and feed management MCP tools."""

import os

from fastmcp import Context, FastMCP

from zotero_mcp.client import (
    clear_active_library,
    get_active_library,
    get_zotero_client,
    set_active_library,
)
from zotero_mcp.utils import clean_html
def list_libraries(*, ctx: Context) -> str:
    """
    List all accessible Zotero libraries.

    In local mode, reads directly from the SQLite database.
    In web mode, queries groups via the Zotero API.

    Returns:
        Markdown-formatted list of libraries with item counts.
    """
    try:
        ctx.info("Listing accessible libraries")
        local = os.getenv("ZOTERO_LOCAL", "").lower() in ["true", "yes", "1"]
        override = get_active_library()

        output = ["# Zotero Libraries", ""]

        # Show active library context
        if override:
            output.append(
                f"> **Active library:** ID={override['library_id']}, "
                f"type={override['library_type']}"
            )
            output.append("")

        if local:
            from zotero_mcp.local_db import LocalZoteroReader

            reader = LocalZoteroReader()
            try:
                libraries = reader.get_libraries()

                # User library
                user_libs = [l for l in libraries if l["type"] == "user"]
                if user_libs:
                    output.append("## User Library")
                    for lib in user_libs:
                        output.append(
                            f"- **My Library** — {lib['itemCount']} items "
                            f"(libraryID={lib['libraryID']})"
                        )
                    output.append("")

                # Group libraries
                group_libs = [l for l in libraries if l["type"] == "group"]
                if group_libs:
                    output.append("## Group Libraries")
                    for lib in group_libs:
                        desc = f" — {lib['groupDescription']}" if lib.get("groupDescription") else ""
                        output.append(
                            f"- **{lib['groupName']}** — {lib['itemCount']} items "
                            f"(groupID={lib['groupID']}){desc}"
                        )
                    output.append("")

                # Feeds
                feed_libs = [l for l in libraries if l["type"] == "feed"]
                if feed_libs:
                    output.append("## RSS Feeds")
                    for lib in feed_libs:
                        output.append(
                            f"- **{lib['feedName']}** — {lib['itemCount']} items "
                            f"(libraryID={lib['libraryID']})"
                        )
                    output.append("")
            finally:
                reader.close()
        else:
            # Web mode: query groups via pyzotero
            zot = get_zotero_client()
            output.append("## User Library")
            output.append(
                f"- **My Library** (libraryID={os.getenv('ZOTERO_LIBRARY_ID', '?')})"
            )
            output.append("")

            try:
                groups = zot.groups()
                if groups:
                    output.append("## Group Libraries")
                    for group in groups:
                        gdata = group.get("data", {})
                        output.append(
                            f"- **{gdata.get('name', 'Unknown')}** "
                            f"(groupID={group.get('id', '?')})"
                        )
                    output.append("")
            except Exception:
                output.append("*Could not retrieve group libraries.*\n")

            output.append("*Note: RSS feeds are only accessible in local mode.*")

        output.append("")
        output.append(
            "Use `zotero_switch_library` to switch to a different library."
        )

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error listing libraries: {str(e)}")
        return f"Error listing libraries: {str(e)}"


def switch_library(
    library_id: str,
    library_type: str = "group",
    *,
    ctx: Context,
) -> str:
    """
    Switch the active library for all subsequent MCP tool calls.

    Args:
        library_id: The library/group ID to switch to.
            For user library: "0" (local mode) or your user ID (web mode).
            For group libraries: the groupID (e.g. "6069773").
        library_type: "user", "group", or "default" to reset to env var defaults.
        ctx: MCP context

    Returns:
        Confirmation message with active library details.
    """
    try:
        if library_type == "default":
            clear_active_library()
            ctx.info("Reset to default library configuration")
            return (
                "Switched back to default library configuration "
                f"(ZOTERO_LIBRARY_ID={os.getenv('ZOTERO_LIBRARY_ID', '0')}, "
                f"ZOTERO_LIBRARY_TYPE={os.getenv('ZOTERO_LIBRARY_TYPE', 'user')})"
            )

        error = validate_library_switch(library_id, library_type)
        if error:
            return error

        set_active_library(library_id, library_type)
        ctx.info(f"Switched to library {library_id} (type={library_type})")

        # Verify the switch works by making a test call
        try:
            zot = get_zotero_client()
            zot.add_parameters(limit=1)
            zot.items()
            return (
                f"Successfully switched to library **{library_id}** "
                f"(type={library_type}). All tools now operate on this library."
            )
        except Exception as e:
            # Roll back on failure
            clear_active_library()
            return (
                f"Error: Could not access library {library_id} "
                f"(type={library_type}): {e}. Reverted to default library."
            )

    except Exception as e:
        ctx.error(f"Error switching library: {str(e)}")
        return f"Error switching library: {str(e)}"


def validate_library_switch(library_id: str, library_type: str) -> str | None:
    """Validate a library switch request before applying it.

    Returns an error message string if the switch should be rejected,
    or None if the switch is valid and should proceed.
    """
    if library_type not in ("user", "group", "feed"):
        return f"Invalid library_type '{library_type}'. Must be 'user', 'group', or 'feed'."

    # In local mode, verify the library actually exists in the database
    local = os.getenv("ZOTERO_LOCAL", "").lower() in ["true", "yes", "1"]
    if local:
        try:
            from zotero_mcp.local_db import LocalZoteroReader

            reader = LocalZoteroReader()
            try:
                libraries = reader.get_libraries()
                if library_type == "group":
                    valid_ids = {str(l["groupID"]) for l in libraries if l["type"] == "group"}
                    if library_id not in valid_ids:
                        return (
                            f"Group '{library_id}' not found. "
                            f"Available groups: {', '.join(sorted(valid_ids))}"
                        )
                elif library_type == "feed":
                    valid_ids = {str(l["libraryID"]) for l in libraries if l["type"] == "feed"}
                    if library_id not in valid_ids:
                        return (
                            f"Feed with libraryID '{library_id}' not found. "
                            f"Available feeds: {', '.join(sorted(valid_ids))}"
                        )
            finally:
                reader.close()
        except Exception:
            pass  # If DB unavailable, skip validation — the test call will catch it

    return None



def list_feeds(*, ctx: Context) -> str:
    """
    List all RSS feed subscriptions from the local Zotero database.

    Returns:
        Markdown-formatted list of RSS feeds.
    """
    try:
        local = os.getenv("ZOTERO_LOCAL", "").lower() in ["true", "yes", "1"]
        if not local:
            return "RSS feeds are only accessible in local mode (ZOTERO_LOCAL=true)."

        ctx.info("Listing RSS feeds")
        from zotero_mcp.local_db import LocalZoteroReader

        reader = LocalZoteroReader()
        try:
            feeds = reader.get_feeds()
            if not feeds:
                return "No RSS feeds found in your Zotero installation."

            output = ["# RSS Feeds", ""]
            for feed in feeds:
                last_check = feed["lastCheck"] or "never"
                error = f" (error: {feed['lastCheckError']})" if feed.get("lastCheckError") else ""
                output.append(f"### {feed['name']}")
                output.append(f"- **URL:** {feed['url']}")
                output.append(f"- **Items:** {feed['itemCount']}")
                output.append(f"- **Last checked:** {last_check}{error}")
                output.append(f"- **Library ID:** {feed['libraryID']}")
                output.append("")

            output.append(
                "Use `zotero_get_feed_items` with a feed's library ID to view its items."
            )
            return "\n".join(output)
        finally:
            reader.close()

    except Exception as e:
        ctx.error(f"Error listing feeds: {str(e)}")
        return f"Error listing feeds: {str(e)}"


def get_feed_items(
    library_id: int,
    limit: int = 20,
    *,
    ctx: Context,
) -> str:
    """
    Retrieve items from a specific RSS feed.

    Args:
        library_id: The libraryID of the feed (from zotero_list_feeds).
        limit: Maximum number of items to return.
        ctx: MCP context

    Returns:
        Markdown-formatted list of feed items.
    """
    try:
        local = os.getenv("ZOTERO_LOCAL", "").lower() in ["true", "yes", "1"]
        if not local:
            return "RSS feed items are only accessible in local mode (ZOTERO_LOCAL=true)."

        ctx.info(f"Fetching items from feed (libraryID={library_id})")
        from zotero_mcp.local_db import LocalZoteroReader

        reader = LocalZoteroReader()
        try:
            # Verify this is actually a feed
            feeds = reader.get_feeds()
            feed_info = next((f for f in feeds if f["libraryID"] == library_id), None)
            if not feed_info:
                valid_ids = [str(f["libraryID"]) for f in feeds]
                return (
                    f"No feed found with libraryID={library_id}. "
                    f"Valid feed IDs: {', '.join(valid_ids)}"
                )

            items = reader.get_feed_items(library_id, limit=limit)
            if not items:
                return f"No items found in feed '{feed_info['name']}'."

            output = [f"# Feed: {feed_info['name']}", f"**URL:** {feed_info['url']}", ""]

            for item in items:
                read_status = "Read" if item.get("readTime") else "Unread"
                title = item.get("title") or "Untitled"
                output.append(f"### {title}")
                output.append(f"- **Status:** {read_status}")
                if item.get("creators"):
                    output.append(f"- **Authors:** {item['creators']}")
                if item.get("url"):
                    output.append(f"- **URL:** {item['url']}")
                output.append(f"- **Added:** {item.get('dateAdded', 'unknown')}")
                if item.get("abstract"):
                    abstract = clean_html(item["abstract"])
                    if len(abstract) > 200:
                        abstract = abstract[:200] + "..."
                    output.append(f"- **Abstract:** {abstract}")
                output.append("")

            return "\n".join(output)
        finally:
            reader.close()

    except Exception as e:
        ctx.error(f"Error fetching feed items: {str(e)}")
        return f"Error fetching feed items: {str(e)}"


def register(mcp: FastMCP) -> None:
    mcp.tool(name="zotero_list_libraries", description="List all accessible Zotero libraries (user library, group libraries, and RSS feeds). Use this to discover available libraries before switching with zotero_switch_library.")(list_libraries)
    mcp.tool(name="zotero_switch_library", description="Switch the active Zotero library context. All subsequent tool calls will operate on the selected library. Use zotero_list_libraries first to see available options. Pass library_type='default' to reset to the original environment variable configuration.")(switch_library)
    mcp.tool(name="zotero_list_feeds", description="List all RSS feed subscriptions in your local Zotero installation. Shows feed names, URLs, item counts, and last check times. Local mode only.")(list_feeds)
    mcp.tool(name="zotero_get_feed_items", description="Get items from a specific RSS feed by its library ID. Use zotero_list_feeds first to find feed library IDs. Local mode only.")(get_feed_items)
