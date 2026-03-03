"""Utility functions for RAG module."""

from typing import Any


def parse_creators_string(creators_str: str) -> list[dict[str, Any]]:
    """Parse creator string in 'Last, First; Last, First' format.

    Args:
        creators_str: Semicolon-separated creator strings in 'Last, First' format

    Returns:
        List of creator dictionaries with 'creatorType', 'firstName', 'lastName',
        or 'creatorType', 'name' for single-name creators.
    """
    if not creators_str:
        return []
    creators: list[dict[str, Any]] = []
    for creator in creators_str.split(";"):
        creator = creator.strip()
        if not creator:
            continue
        if "," in creator:
            last, first = creator.split(",", 1)
            creators.append(
                {
                    "creatorType": "author",
                    "firstName": first.strip(),
                    "lastName": last.strip(),
                }
            )
        else:
            creators.append({"creatorType": "author", "name": creator})
    return creators
