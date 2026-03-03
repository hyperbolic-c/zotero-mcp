"""Compatibility module for rag submodule imports.

This module provides references that can be monkeypatched by tests
to control behavior without circular import issues.
"""

# Default imports - these will be used if not overridden by __getattr__
# Note: Don't define module-level attributes here so __getattr__ is used


def __getattr__(name: str):
    """Dynamic attribute resolution to support test monkeypatching."""
    if name == "is_local_mode":
        from zotero_mcp.utils import is_local_mode as func
        # Check if advanced_rag has been monkeypatched
        try:
            import zotero_mcp.rag.advanced_rag as advanced_rag
            if hasattr(advanced_rag, "is_local_mode"):
                return advanced_rag.is_local_mode
        except (ImportError, AttributeError):
            pass
        return func
    if name == "LocalZoteroReader":
        from zotero_mcp.local_db import LocalZoteroReader as cls
        # Check if advanced_rag has been monkeypatched
        try:
            import zotero_mcp.rag.advanced_rag as advanced_rag
            if hasattr(advanced_rag, "LocalZoteroReader"):
                return advanced_rag.LocalZoteroReader
        except (ImportError, AttributeError):
            pass
        return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
