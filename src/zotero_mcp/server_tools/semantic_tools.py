"""Semantic search MCP tools."""

import json
from pathlib import Path

from fastmcp import Context, FastMCP

from zotero_mcp.utils import format_creators
def semantic_search(
    query: str,
    limit: int = 10,
    filters: dict[str, str] | str | None = None,
    abstract_max_chars: int | None = None,
    matched_content_max_chars: int | None = None,
    include_citation_references: bool = True,
    citation_max_items_per_result: int = 8,
    *,
    ctx: Context
) -> str:
    """
    Perform semantic search over your Zotero library.

    Args:
        query: Search query text - can be concepts, topics, or natural language descriptions
        limit: Maximum number of results to return (default: 10)
        filters: Optional metadata filters as dict or JSON string. Example: {"item_type": "note"}
        abstract_max_chars: Optional max characters for abstract display. None means no truncation.
        matched_content_max_chars: Optional max characters for matched content display. None means no truncation.
        include_citation_references: Whether to append references cited in matched content.
        citation_max_items_per_result: Max number of appended references per search result.
        ctx: MCP context

    Returns:
        Markdown-formatted search results with similarity scores
    """
    def _format_with_optional_limit(text: str, max_chars: int | None) -> str:
        if max_chars is None:
            return text
        if len(text) > max_chars:
            return text[:max_chars] + "..."
        return text

    try:
        if not query.strip():
            return "Error: Search query cannot be empty"

        if abstract_max_chars is not None and abstract_max_chars <= 0:
            return "Error: abstract_max_chars must be a positive integer when provided"
        if matched_content_max_chars is not None and matched_content_max_chars <= 0:
            return "Error: matched_content_max_chars must be a positive integer when provided"
        if citation_max_items_per_result <= 0:
            return "Error: citation_max_items_per_result must be a positive integer"

        # Parse and validate filters parameter
        if filters is not None:
            # Handle JSON string input
            if isinstance(filters, str):
                try:
                    filters = json.loads(filters)
                    ctx.info(f"Parsed JSON string filters: {filters}")
                except json.JSONDecodeError as e:
                    return f"Error: Invalid JSON in filters parameter: {str(e)}"

            # Validate it's a dictionary
            if not isinstance(filters, dict):
                return "Error: filters parameter must be a dictionary or JSON string. Example: {\"item_type\": \"note\"}"

            # Automatically translate common field names
            if "itemType" in filters:
                filters["item_type"] = filters.pop("itemType")
                ctx.info(f"Automatically translated 'itemType' to 'item_type': {filters}")

            # Additional field name translations can be added here
            # Example: if "creatorType" in filters:
            #     filters["creator_type"] = filters.pop("creatorType")

        ctx.info(f"Performing semantic search for: '{query}'")

        # Import semantic search module
        from zotero_mcp.semantic_search import create_semantic_search
        from pathlib import Path

        # Determine config path
        config_path = Path.home() / ".config" / "zotero-mcp" / "config.json"

        # Create semantic search instance
        search = create_semantic_search(str(config_path))

        # Perform search
        results = search.search(
            query=query,
            limit=limit,
            filters=filters,
            include_citation_references=include_citation_references,
            citation_max_items_per_result=citation_max_items_per_result,
        )

        if results.get("error"):
            return f"Semantic search error: {results['error']}"

        search_results = results.get("results", [])

        if not search_results:
            return f"No semantically similar items found for query: '{query}'"

        # Format results as markdown
        output = [f"# Semantic Search Results for '{query}'", ""]
        output.append(f"Found {len(search_results)} similar items:")
        output.append("")

        for i, result in enumerate(search_results, 1):
            similarity_score = result.get("similarity_score", 0)
            _ = result.get("metadata", {})
            zotero_item = result.get("zotero_item", {})

            if zotero_item:
                data = zotero_item.get("data", {})
                title = data.get("title", "Untitled")
                item_type = data.get("itemType", "unknown")
                key = result.get("item_key", "")

                # Format creators
                creators = data.get("creators", [])
                creators_str = format_creators(creators)

                output.append(f"## {i}. {title}")
                output.append(f"**Similarity Score:** {similarity_score:.3f}")
                output.append(f"**Type:** {item_type}")
                output.append(f"**Item Key:** {key}")
                output.append(f"**Authors:** {creators_str}")

                # Add date if available
                if date := data.get("date"):
                    output.append(f"**Date:** {date}")

                # Add abstract if present (optionally truncated by caller)
                if abstract := data.get("abstractNote"):
                    output.append(
                        f"**Abstract:** {_format_with_optional_limit(abstract, abstract_max_chars)}"
                    )

                # Add tags if present
                if tags := data.get("tags"):
                    tag_list = [f"`{tag['tag']}`" for tag in tags]
                    if tag_list:
                        output.append(f"**Tags:** {' '.join(tag_list)}")

                # Show matched text snippet
                matched_text = result.get("matched_text", "")
                if matched_text:
                    output.append(
                        f"**Matched Content:** {_format_with_optional_limit(matched_text, matched_content_max_chars)}"
                    )
                if include_citation_references:
                    resolved = result.get("resolved_citations", [])[:citation_max_items_per_result]
                    if resolved:
                        output.append("**Citations Referenced in Matched Content:**")
                        for citation in resolved:
                            ref_num = citation.get("ref_num")
                            ref_text = citation.get("ref_text", "")
                            output.append(f"- [{ref_num}] {ref_text}")

                output.append("")  # Empty line between items
            else:
                # Fallback if full Zotero item not available
                output.append(f"## {i}. Item {result.get('item_key', 'Unknown')}")
                output.append(f"**Similarity Score:** {similarity_score:.3f}")
                if error := result.get("error"):
                    output.append(f"**Error:** {error}")
                output.append("")

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error in semantic search: {str(e)}")
        return f"Error in semantic search: {str(e)}"


def update_search_database(
    force_rebuild: bool = False,
    limit: int | None = None,
    *,
    ctx: Context
) -> str:
    """
    Update the semantic search database.

    Args:
        force_rebuild: Whether to rebuild the entire database from scratch
        limit: Limit number of items to process (useful for testing)
        ctx: MCP context

    Returns:
        Update status and statistics
    """
    try:
        ctx.info("Starting semantic search database update...")

        # Import semantic search and indexer modules
        from zotero_mcp.semantic_search import ZoteroSemanticSearch
        from zotero_mcp.indexer import ZoteroIndexer
        from pathlib import Path

        # Determine config path
        config_path = Path.home() / ".config" / "zotero-mcp" / "config.json"

        # Create semantic search instance to get configuration
        search = ZoteroSemanticSearch(config_path=str(config_path))
        
        # Create indexer instance (can also use search.indexer if preferred)
        indexer = search.indexer

        # Perform update with no fulltext extraction (for speed)
        stats = indexer.update_database(
            force_full_rebuild=force_rebuild,
            limit=limit,
            extract_fulltext=False
        )

        # Format results
        output = ["# Database Update Results", ""]

        if stats.get("error"):
            output.append(f"**Error:** {stats['error']}")
        else:
            retriever_mode = stats.get("retriever_mode", "legacy_metadata")
            added_count = stats.get("added_items", stats.get("added_chunks", 0))
            updated_count = stats.get("updated_items", stats.get("updated_chunks", 0))
            added_label = "Added chunks" if retriever_mode == "advanced_rag" else "Added"
            updated_label = "Updated chunks" if retriever_mode == "advanced_rag" else "Updated"
            output.append(f"**Total items:** {stats.get('total_items', 0)}")
            output.append(f"**Processed:** {stats.get('processed_items', 0)}")
            output.append(f"**{added_label}:** {added_count}")
            output.append(f"**{updated_label}:** {updated_count}")
            output.append(f"**Skipped:** {stats.get('skipped_items', 0)}")
            output.append(f"**Errors:** {stats.get('errors', 0)}")
            output.append(f"**Duration:** {stats.get('duration', 'Unknown')}")

            if stats.get('start_time'):
                output.append(f"**Started:** {stats['start_time']}")
            if stats.get('end_time'):
                output.append(f"**Completed:** {stats['end_time']}")

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error updating search database: {str(e)}")
        return f"Error updating search database: {str(e)}"


def get_search_database_status(*, ctx: Context) -> str:
    """
    Get semantic search database status.

    Args:
        ctx: MCP context

    Returns:
        Database status information
    """
    try:
        ctx.info("Getting semantic search database status...")

        # Import semantic search module
        from zotero_mcp.semantic_search import create_semantic_search
        from pathlib import Path

        # Determine config path
        config_path = Path.home() / ".config" / "zotero-mcp" / "config.json"

        # Create semantic search instance
        search = create_semantic_search(str(config_path))

        # Get status
        status = search.get_database_status()

        # Format results
        output = ["# Semantic Search Database Status", ""]

        collection_info = status.get("collection_info", {})
        output.append("## Collection Information")
        output.append(f"**Name:** {collection_info.get('name', 'Unknown')}")
        output.append(f"**Document Count:** {collection_info.get('count', 0)}")
        output.append(f"**Embedding Model:** {collection_info.get('embedding_model', 'Unknown')}")
        output.append(f"**Database Path:** {collection_info.get('persist_directory', 'Unknown')}")
        output.append(f"**Retriever Mode:** {status.get('retriever_mode', 'legacy_metadata')}")
        if status.get("reranker_status"):
            output.append(f"**Reranker:** {status.get('reranker_status')}")

        if collection_info.get('error'):
            output.append(f"**Error:** {collection_info['error']}")

        refs_collection_info = status.get("refs_collection_info", {})
        if refs_collection_info:
            output.append("")
            output.append("## References Collection")
            output.append(f"**Name:** {refs_collection_info.get('name', 'Unknown')}")
            output.append(f"**Document Count:** {refs_collection_info.get('count', 0)}")
            if refs_collection_info.get('error'):
                output.append(f"**Error:** {refs_collection_info['error']}")

        output.append("")

        update_config = status.get("update_config", {})
        output.append("## Update Configuration")
        output.append(f"**Auto Update:** {update_config.get('auto_update', False)}")
        output.append(f"**Frequency:** {update_config.get('update_frequency', 'manual')}")
        output.append(f"**Last Update:** {update_config.get('last_update', 'Never')}")
        output.append(f"**Should Update Now:** {status.get('should_update', False)}")

        if update_config.get('update_days'):
            output.append(f"**Update Interval:** Every {update_config['update_days']} days")

        return "\n".join(output)

    except Exception as e:
        ctx.error(f"Error getting database status: {str(e)}")
        return f"Error getting database status: {str(e)}"


# --- Minimal wrappers for ChatGPT connectors ---


def register(mcp: FastMCP) -> None:
    mcp.tool(name="zotero_semantic_search", description="Prioritized search tool. Perform semantic search over your Zotero library using AI-powered embeddings.")(semantic_search)
    mcp.tool(name="zotero_update_search_database", description="Update the semantic search database with latest Zotero items.")(update_search_database)
    mcp.tool(name="zotero_get_search_database_status", description="Get status information about the semantic search database.")(get_search_database_status)
