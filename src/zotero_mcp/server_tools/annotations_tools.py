"""Annotation tool registration and compatibility exports."""

from fastmcp import FastMCP

from zotero_mcp.server_tools.annotation_create_tools import create_annotation
from zotero_mcp.server_tools.annotations_read_tools import _get_annotations, get_annotations


def register(mcp: FastMCP) -> None:
    mcp.tool(name="zotero_get_annotations", description="Get all annotations for a specific item or across your entire Zotero library.")(get_annotations)
    mcp.tool(name="zotero_create_annotation", description="Create a highlight annotation on a PDF or EPUB attachment with optional comment.")(create_annotation)
