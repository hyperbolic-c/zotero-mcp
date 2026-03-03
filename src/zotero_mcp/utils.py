from typing import List, Dict, Any, Optional
import logging
import os
import re
from contextlib import contextmanager

html_re = re.compile(r"<.*?>")

# Rich progress display (optional import, fails gracefully if rich not available)
try:
    from rich.console import Console as RichConsole
    from rich.progress import (
        Progress,
        SpinnerColumn,
        TextColumn,
        BarColumn,
        TaskProgressColumn,
        TimeRemainingColumn,
    )
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


# Logger names to suppress during indexing
INDEXING_LOGGERS = [
    "zotero_mcp.chroma_client",
    "zotero_mcp.indexer",
    "zotero_mcp.rag.advanced_rag",
]


class IndexingProgress:
    """Generic indexing progress display using Rich.

    Usage IndexingProgress(":
        withIndexing items", total=100) as progress:
            for i in range(100):
                progress.update(processed=i+1, indexed=i, skipped=0, errors=0)
    """

    def __init__(
        self,
        description: str = "Processing",
        total: int = 0,
        show_stats: bool = True,
        console: Optional[Any] = None,
        suppress_logging: bool = True,
    ):
        self.description = description
        self.total = total
        self.show_stats = show_stats
        self._console = console
        self._progress: Optional[Any] = None
        self._task = None
        self._suppress_logging = suppress_logging
        self._previous_levels: Dict[str, int] = {}

    def _suppress_verbose_logging(self):
        """Suppress INFO/WARNING logs during indexing to keep progress bar clean."""
        for logger_name in INDEXING_LOGGERS:
            logger = logging.getLogger(logger_name)
            self._previous_levels[logger_name] = logger.level
            # Only suppress if currently at default level (not explicitly set)
            if logger.level < logging.WARNING:
                logger.setLevel(logging.WARNING)

    def _restore_logging(self):
        """Restore previous logging levels."""
        for logger_name, level in self._previous_levels.items():
            logger = logging.getLogger(logger_name)
            logger.setLevel(level)

    def __enter__(self):
        if self._suppress_logging:
            self._suppress_verbose_logging()

        if not RICH_AVAILABLE:
            return self

        from rich.console import Console
        from rich.progress import (
            Progress,
            SpinnerColumn,
            TextColumn,
            BarColumn,
            TaskProgressColumn,
            TimeRemainingColumn,
        )

        self._console = self._console or Console(stderr=True)

        columns = [
            SpinnerColumn(),
            TextColumn(f"[bold blue]{{task.description}}[/bold blue]"),
            BarColumn(),
            TaskProgressColumn(),
        ]

        if self.show_stats:
            columns.append(TextColumn("[dim]{task.fields[stats]}[/dim]"))

        columns.append(TimeRemainingColumn())

        self._progress = Progress(*columns, console=self._console)
        self._progress.__enter__()

        stats = "" if self.show_stats else None
        self._task = self._progress.add_task(
            self.description,
            total=self.total,
            stats=stats,
        )

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._suppress_logging:
            self._restore_logging()
        if self._progress:
            self._progress.__exit__(exc_type, exc_val, exc_tb)
        return False

    def update(
        self,
        processed: Optional[int] = None,
        indexed: Optional[int] = None,
        skipped: Optional[int] = None,
        errors: Optional[int] = None,
    ):
        """Update progress display.

        Args:
            processed: Number of items processed
            indexed: Number of items indexed
            skipped: Number of items skipped
            errors: Number of errors encountered
        """
        if not self._progress or self._task is None:
            # Fallback to print if Rich not available
            if processed is not None:
                print(f"Progress: {processed}/{self.total}", file=__import__("sys").stderr)
            return

        fields = {}
        if self.show_stats:
            stats_parts = []
            if indexed is not None:
                stats_parts.append(f"indexed: {indexed}")
            if skipped is not None:
                stats_parts.append(f"skipped: {skipped}")
            if errors is not None:
                stats_parts.append(f"errors: {errors}")
            fields["stats"] = " | ".join(stats_parts) if stats_parts else ""

        self._progress.update(
            self._task,
            advance=1 if processed is None else None,
            **fields,
        )

    def set_total(self, total: int):
        """Update the total count."""
        self.total = total
        if self._progress and self._task is not None:
            self._progress.update(self._task, total=total)

    @property
    def completed(self) -> int:
        """Return number of completed items."""
        if self._progress and self._task is not None:
            return self._progress.tasks[self._task].completed
        return 0

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