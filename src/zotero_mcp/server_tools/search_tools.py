"""Search tools for Zotero MCP."""

import json
from typing import Literal

from fastmcp import Context, FastMCP

from zotero_mcp.client import get_zotero_client
from zotero_mcp.server_tools.common import matches_condition
from zotero_mcp.utils import format_creators


def search_items(
    query: str,
    qmode: Literal["titleCreatorYear", "everything"] = "titleCreatorYear",
    item_type: str = "-attachment",  # Exclude attachments by default
    limit: int | str | None = 10,
    tag: list[str] | None = None,
    *,
    ctx: Context
) -> str:
    """
    Search for items in your Zotero library.

    Args:
        query: Search query string
        qmode: Query mode (titleCreatorYear or everything)
        item_type: Type of items to search for. Use "-attachment" to exclude attachments.
        limit: Maximum number of results to return
        tag: List of tags conditions to filter by
        ctx: MCP context

    Returns:
        Markdown-formatted search results
    """
    try:
        if not query.strip():
            return "Error: Search query cannot be empty"

        tag_condition_str = ""
        if tag:
            tag_condition_str = f" with tags: '{', '.join(tag)}'"
        else:
            tag = []

        ctx.info(f"Searching Zotero for '{query}'{tag_condition_str}")
        zot = get_zotero_client()

        if isinstance(limit, str):
            limit = int(limit)

        # Search using the query parameters
        zot.add_parameters(q=query, qmode=qmode, itemType=item_type, limit=limit, tag=tag)
        results = zot.items()

        if not results:
            return f"No items found matching query: '{query}'{tag_condition_str}"

        # Format results as markdown
        output = [f"# Search Results for '{query}'", f"{tag_condition_str}", ""]

        for i, item in enumerate(results, 1):
            data = item.get("data", {})
            title = data.get("title", "Untitled")
            item_type = data.get("itemType", "unknown")
            date = data.get("date", "No date")
            key = item.get("key", "")

            # Format creators
            creators = data.get("creators", [])
            creators_str = format_creators(creators)

            # Build the formatted entry
            output.append(f"## {i}. {title}")
            output.append(f"**Type:** {item_type}")
            output.append(f"**Item Key:** {key}")
            output.append(f"**Date:** {date}")
            output.append(f"**Authors:** {creators_str}")

            # Add abstract snippet if present
            if abstract := data.get("abstractNote"):
                # Limit abstract length for search results
                abstract_snippet = abstract[:200] + "..." if len(abstract) > 200 else abstract
                output.append(f"**Abstract:** {abstract_snippet}")

            # Add tags if present
            if tags := data.get("tags"):
                tag_list = [f"`{tag['tag']}`" for tag in tags]
                if tag_list:
                    output.append(f"**Tags:** {' '.join(tag_list)}")

            output.append("")  # Empty line between items

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error searching Zotero: {str(e)}")
        return f"Error searching Zotero: {str(e)}"


def search_by_tag(
    tag: list[str],
    item_type: str = "-attachment",
    limit: int | str | None = 10,
    *,
    ctx: Context
) -> str:
    """
    Search for items in your Zotero library by tag.
    Conditions are ANDed, each term supports disjunction (`OR`) and exclusion (`-`).

    Args:
        tag: List of tag conditions. Items are returned only if they satisfy
            ALL conditions in the list. Each tag condition can be expressed
            in two ways:
                As alternatives: tag1 OR tag2 (matches items with either tag1 OR tag2)
                As exclusions: -tag (matches items that do NOT have this tag)
            For example, a tag field with ["research OR important", "-draft"] would
            return items that:
                Have either "research" OR "important" tags, AND
                Do NOT have the "draft" tag
        item_type: Type of items to search for. Use "-attachment" to exclude attachments.
        limit: Maximum number of results to return
        ctx: MCP context

    Returns:
        Markdown-formatted search results
    """
    try:
        if not tag:
            return "Error: Tag cannot be empty"

        ctx.info(f"Searching Zotero for tag '{tag}'")
        zot = get_zotero_client()

        if isinstance(limit, str):
            limit = int(limit)

        # Search using the query parameters
        zot.add_parameters(q="", tag=tag, itemType=item_type, limit=limit)
        results = zot.items()

        if not results:
            return f"No items found with tag: '{tag}'"

        # Format results as markdown
        output = [f"# Search Results for Tag: '{tag}'", ""]

        for i, item in enumerate(results, 1):
            data = item.get("data", {})
            title = data.get("title", "Untitled")
            item_type = data.get("itemType", "unknown")
            date = data.get("date", "No date")
            key = item.get("key", "")

            # Format creators
            creators = data.get("creators", [])
            creators_str = format_creators(creators)

            # Build the formatted entry
            output.append(f"## {i}. {title}")
            output.append(f"**Type:** {item_type}")
            output.append(f"**Item Key:** {key}")
            output.append(f"**Date:** {date}")
            output.append(f"**Authors:** {creators_str}")

            # Add abstract snippet if present
            if abstract := data.get("abstractNote"):
                # Limit abstract length for search results
                abstract_snippet = abstract[:200] + "..." if len(abstract) > 200 else abstract
                output.append(f"**Abstract:** {abstract_snippet}")

            # Add tags if present
            if tags := data.get("tags"):
                tag_list = [f"`{tag['tag']}`" for tag in tags]
                if tag_list:
                    output.append(f"**Tags:** {' '.join(tag_list)}")

            output.append("")  # Empty line between items

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error searching Zotero: {str(e)}")
        return f"Error searching Zotero: {str(e)}"


def get_recent(
    limit: int | str = 10,
    *,
    ctx: Context
) -> str:
    """
    Get recently added items to your Zotero library.

    Args:
        limit: Number of items to return
        ctx: MCP context

    Returns:
        Markdown-formatted list of recent items
    """
    try:
        ctx.info(f"Fetching {limit} recent items")
        zot = get_zotero_client()

        if isinstance(limit, str):
            limit = int(limit)

        # Ensure limit is a reasonable number
        if limit <= 0:
            limit = 10
        elif limit > 100:
            limit = 100

        # Get recent items
        items = zot.items(limit=limit, sort="dateAdded", direction="desc")
        if not items:
            return "No items found in your Zotero library."

        # Format items as markdown
        output = [f"# {limit} Most Recently Added Items", ""]

        for i, item in enumerate(items, 1):
            data = item.get("data", {})
            title = data.get("title", "Untitled")
            item_type = data.get("itemType", "unknown")
            date = data.get("date", "No date")
            key = item.get("key", "")
            date_added = data.get("dateAdded", "Unknown")

            # Format creators
            creators = data.get("creators", [])
            creators_str = format_creators(creators)

            # Build the formatted entry
            output.append(f"## {i}. {title}")
            output.append(f"**Type:** {item_type}")
            output.append(f"**Item Key:** {key}")
            output.append(f"**Date:** {date}")
            output.append(f"**Added:** {date_added}")
            output.append(f"**Authors:** {creators_str}")

            output.append("")  # Empty line between items

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error fetching recent items: {str(e)}")
        return f"Error fetching recent items: {str(e)}"


def advanced_search(
    conditions: list[dict[str, str]],
    join_mode: Literal["all", "any"] = "all",
    sort_by: str | None = None,
    sort_direction: Literal["asc", "desc"] = "asc",
    limit: int | str = 50,
    *,
    ctx: Context
) -> str:
    """
    Perform an advanced search with multiple criteria.

    Args:
        conditions: List of search condition dictionaries, each containing:
                   - field: The field to search (title, creator, date, tag, etc.)
                   - operation: The operation to perform (is, isNot, contains, etc.)
                   - value: The value to search for
        join_mode: Whether all conditions must match ("all") or any condition can match ("any")
        sort_by: Field to sort by (dateAdded, dateModified, title, creator, etc.)
        sort_direction: Direction to sort (asc or desc)
        limit: Maximum number of results to return
        ctx: MCP context

    Returns:
        Markdown-formatted search results
    """
    try:
        if isinstance(conditions, str):
            try:
                conditions = json.loads(conditions)
            except json.JSONDecodeError as parse_error:
                return (
                    "Error: conditions must be valid JSON when provided as a string "
                    f"({parse_error})"
                )

        if not isinstance(conditions, list) or not conditions:
            return "Error: No search conditions provided"

        if join_mode not in {"all", "any"}:
            return "Error: join_mode must be either 'all' or 'any'"

        if isinstance(limit, str):
            limit = int(limit)
        if limit <= 0:
            return "Error: limit must be greater than 0"
        if limit > 500:
            limit = 500

        ctx.info(f"Performing advanced search with {len(conditions)} conditions")
        zot = get_zotero_client()

        valid_operations = {
            "is",
            "isNot",
            "contains",
            "doesNotContain",
            "beginsWith",
            "endsWith",
            "isGreaterThan",
            "isLessThan",
            "isBefore",
            "isAfter",
        }

        parsed_conditions: list[dict[str, str]] = []
        for i, condition in enumerate(conditions, 1):
            if not isinstance(condition, dict):
                return f"Error: Condition {i} must be an object"
            if "field" not in condition or "operation" not in condition or "value" not in condition:
                return (
                    f"Error: Condition {i} is missing required fields "
                    "(field, operation, value)"
                )

            field = str(condition["field"]).strip()
            operation = str(condition["operation"]).strip()
            value = str(condition["value"]).strip()

            if operation not in valid_operations:
                return (
                    f"Error: Unsupported operation '{operation}' in condition {i}. "
                    f"Supported: {', '.join(sorted(valid_operations))}"
                )
            if not field:
                return f"Error: Condition {i} has an empty field"

            parsed_conditions.append(
                {"field": field, "operation": operation, "value": value}
            )

        # Execute advanced search by iterating items and filtering client-side.
        results = []
        batch_size = 100
        start = 0
        while True:
            batch = zot.items(start=start, limit=batch_size)
            if not batch:
                break

            for item in batch:
                data = item.get("data", {})
                if data.get("itemType") in {"attachment", "note", "annotation"}:
                    continue

                checks = [matches_condition(data, c) for c in parsed_conditions]
                matched = all(checks) if join_mode == "all" else any(checks)
                if matched:
                    results.append(item)

            if len(batch) < batch_size:
                break
            start += batch_size

        if sort_by:
            sort_field = sort_by.strip()
            reverse = sort_direction == "desc"

            def _sort_key(item: dict[str, object]) -> str:
                data = item.get("data", {}) if isinstance(item, dict) else {}
                if sort_field in {"creator", "author"}:
                    return format_creators(data.get("creators", []))
                return str(data.get(sort_field, "")).lower()

            results.sort(key=_sort_key, reverse=reverse)

        if not results:
            return "No items found matching the search criteria."

        results = results[:limit]

        output = ["# Advanced Search Results", ""]
        output.append(f"Found {len(results)} items matching the search criteria:")
        output.append("")
        output.append("## Search Criteria")
        output.append(f"Join mode: {join_mode.upper()}")
        for i, condition in enumerate(parsed_conditions, 1):
            output.append(
                f"{i}. {condition['field']} {condition['operation']} \"{condition['value']}\""
            )
        output.append("")
        output.append("## Results")

        for i, item in enumerate(results, 1):
            data = item.get("data", {})
            title = data.get("title", "Untitled")
            item_type = data.get("itemType", "unknown")
            date = data.get("date", "No date")
            key = item.get("key", "")

            creators = data.get("creators", [])
            creators_str = format_creators(creators)

            output.append(f"### {i}. {title}")
            output.append(f"**Type:** {item_type}")
            output.append(f"**Item Key:** {key}")
            output.append(f"**Date:** {date}")
            output.append(f"**Authors:** {creators_str}")

            if abstract := data.get("abstractNote"):
                abstract_snippet = abstract[:150] + "..." if len(abstract) > 150 else abstract
                output.append(f"**Abstract:** {abstract_snippet}")

            if tags := data.get("tags"):
                tag_list = [f"`{tag['tag']}`" for tag in tags]
                if tag_list:
                    output.append(f"**Tags:** {' '.join(tag_list)}")

            output.append("")

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error in advanced search: {str(e)}")
        return f"Error in advanced search: {str(e)}"


def batch_update_tags(
    query: str,
    add_tags: list[str] | str | None = None,
    remove_tags: list[str] | str | None = None,
    limit: int | str = 50,
    *,
    ctx: Context
) -> str:
    """
    Batch update tags across multiple items matching a search query.

    Args:
        query: Search query to find items to update
        add_tags: List of tags to add to matched items (can be list or JSON string)
        remove_tags: List of tags to remove from matched items (can be list or JSON string)
        limit: Maximum number of items to process
        ctx: MCP context

    Returns:
        Summary of the batch update
    """
    try:
        if not query:
            return "Error: Search query cannot be empty"

        if not add_tags and not remove_tags:
            return "Error: You must specify either tags to add or tags to remove"

        def _normalize_tag_list(
            raw_value: list[str] | str | None, field_name: str
        ) -> list[str]:
            if raw_value is None:
                return []

            parsed_value = raw_value
            if isinstance(parsed_value, str):
                try:
                    parsed_value = json.loads(parsed_value)
                    ctx.info(f"Parsed {field_name} from JSON string: {parsed_value}")
                except json.JSONDecodeError:
                    raise ValueError(
                        f"{field_name} appears to be malformed JSON: {raw_value}"
                    )

            if not isinstance(parsed_value, list):
                raise ValueError(
                    f"{field_name} must be a JSON array or a list of strings"
                )

            normalized = []
            for tag_value in parsed_value:
                if not isinstance(tag_value, str):
                    raise ValueError(f"{field_name} entries must all be strings")
                stripped = tag_value.strip()
                if stripped:
                    normalized.append(stripped)
            return normalized

        try:
            add_tags = _normalize_tag_list(add_tags, "add_tags")
            remove_tags = _normalize_tag_list(remove_tags, "remove_tags")
        except ValueError as validation_error:
            return f"Error: {validation_error}"

        if not add_tags and not remove_tags:
            return "Error: After parsing, no valid tags were provided to add or remove"

        ctx.info(f"Batch updating tags for items matching '{query}'")
        zot = get_zotero_client()

        if isinstance(limit, str):
            limit = int(limit)

        # Search for items matching the query
        zot.add_parameters(q=query, limit=limit)
        items = zot.items()

        if not items:
            return f"No items found matching query: '{query}'"

        # Initialize counters
        updated_count = 0
        skipped_count = 0
        added_tag_counts = {tag: 0 for tag in (add_tags or [])}
        removed_tag_counts = {tag: 0 for tag in (remove_tags or [])}

        # Process each item
        for item in items:
            # Skip attachments if they were included in the results
            if item["data"].get("itemType") == "attachment":
                skipped_count += 1
                continue

            # Get current tags
            current_tags = item["data"].get("tags", [])
            current_tag_values = {t["tag"] for t in current_tags}

            # Track if this item needs to be updated
            needs_update = False

            # Process tags to remove
            if remove_tags:
                new_tags = []
                for tag_obj in current_tags:
                    tag = tag_obj["tag"]
                    if tag in remove_tags:
                        removed_tag_counts[tag] += 1
                        needs_update = True
                    else:
                        new_tags.append(tag_obj)
                current_tags = new_tags

            # Process tags to add
            if add_tags:
                for tag in add_tags:
                    if tag and tag not in current_tag_values:
                        current_tags.append({"tag": tag})
                        added_tag_counts[tag] += 1
                        needs_update = True

            # Update the item if needed
            if needs_update:
                try:
                    item["data"]["tags"] = current_tags
                    ctx.info(f"Updating item {item.get('key', 'unknown')} with tags: {current_tags}")
                    result = zot.update_item(item)
                    ctx.info(f"Update result: {result}")
                    updated_count += 1
                except Exception as e:
                    ctx.error(f"Failed to update item {item.get('key', 'unknown')}: {str(e)}")
                    # Continue with other items instead of failing completely
                    skipped_count += 1
            else:
                skipped_count += 1

        # Format the response
        response = ["# Batch Tag Update Results", ""]
        response.append(f"Query: '{query}'")
        response.append(f"Items processed: {len(items)}")
        response.append(f"Items updated: {updated_count}")
        response.append(f"Items skipped: {skipped_count}")

        if add_tags:
            response.append("\n## Tags Added")
            for tag, count in added_tag_counts.items():
                response.append(f"- `{tag}`: {count} items")

        if remove_tags:
            response.append("\n## Tags Removed")
            for tag, count in removed_tag_counts.items():
                response.append(f"- `{tag}`: {count} items")

        return "\n".join(response)

    except Exception as e:
        ctx.error(f"Error in batch tag update: {str(e)}")
        return f"Error in batch tag update: {str(e)}"


def register(mcp: FastMCP) -> None:
    """Register search tools."""
    mcp.tool(
        name="zotero_search_items",
        description="Search for items in your Zotero library, given a query string."
    )(search_items)

    mcp.tool(
        name="zotero_search_by_tag",
        description="Search for items in your Zotero library by tag. Conditions are ANDed, each term supports disjunction (`OR`) and exclusion (`-`)."
    )(search_by_tag)

    mcp.tool(
        name="zotero_get_recent",
        description="Get recently added items to your Zotero library."
    )(get_recent)

    mcp.tool(
        name="zotero_batch_update_tags",
        description="Batch update tags across multiple items matching a search query."
    )(batch_update_tags)

    mcp.tool(
        name="zotero_advanced_search",
        description="Perform an advanced search with multiple criteria."
    )(advanced_search)
