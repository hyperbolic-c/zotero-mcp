from typing import List, Dict
import os
import re

html_re = re.compile(r"<.*?>")

def format_creators(creators: list[dict[str, str]]) -> str:
    """
    Format creator names into a string.

    Args:
        creators: List of creator objects from Zotero.

    Returns:
        Formatted string with creator names.
    """
    names = []
    for creator in creators:
        if "firstName" in creator and "lastName" in creator:
            names.append(f"{creator['lastName']}, {creator['firstName']}")
        elif "name" in creator:
            names.append(creator["name"])
    return "; ".join(names) if names else "No authors listed"


def is_local_mode() -> bool:
    """Return True if running in local mode.

    Local mode is enabled when environment variable `ZOTERO_LOCAL` is set to a
    truthy value ("true", "yes", or "1", case-insensitive).
    """
    value = os.getenv("ZOTERO_LOCAL", "")
    return value.lower() in {"true", "yes", "1"}

def parse_creators_string(creators_str: str) -> list[dict[str, str]]:
    """Parse local DB creators string ('Last, First; Last2, First2') into API creator objects."""
    if not creators_str:
        return []

    creators: list[dict[str, str]] = []
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


def clean_html(raw_html: str) -> str:
    """
    Remove HTML tags from a string.

    Args:
        raw_html: String containing HTML content.
    Returns:
        Cleaned string without HTML tags.
    """
    clean_text = re.sub(html_re, "", raw_html)
    return clean_text