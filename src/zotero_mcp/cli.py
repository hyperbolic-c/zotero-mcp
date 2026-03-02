"""
Command-line interface for Zotero MCP server.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.theme import Theme

from zotero_mcp.server import mcp

# Setup rich console with custom theme
custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow",
    "error": "red",
    "success": "green",
    "bold": "bold",
})
console = Console(theme=custom_theme)


def obfuscate_sensitive_value(value, keep_chars=4):
    """Obfuscate sensitive values by showing only the first few characters."""
    if not value or not isinstance(value, str):
        return value
    if len(value) <= keep_chars:
        return "*" * len(value)
    return value[:keep_chars] + "*" * (len(value) - keep_chars)


def obfuscate_config_for_display(config):
    """Create a copy of config with sensitive values obfuscated."""
    if not isinstance(config, dict):
        return config

    obfuscated = config.copy()
    sensitive_keys = ["ZOTERO_API_KEY", "ZOTERO_LIBRARY_ID", "API_KEY", "LIBRARY_ID"]

    for key in sensitive_keys:
        if key in obfuscated:
            obfuscated[key] = obfuscate_sensitive_value(obfuscated[key])

    return obfuscated


def load_claude_desktop_env_vars():
    """Load Zotero environment variables from Claude Desktop config unless globally disabled."""
    # Global guard to skip Claude detection entirely
    if str(os.environ.get("ZOTERO_NO_CLAUDE", "")).lower() in ("1", "true", "yes"):
        return {}
    from zotero_mcp.setup_helper import find_claude_config

    try:
        config_path = find_claude_config()
        if not config_path or not config_path.exists():
            return {}

        with open(config_path) as f:
            config = json.load(f)

        # Extract Zotero MCP server environment variables
        mcp_servers = config.get("mcpServers", {})
        zotero_config = mcp_servers.get("zotero", {})
        env_vars = zotero_config.get("env", {})

        return env_vars

    except Exception:
        return {}


def load_standalone_env_vars():
    """Load environment variables from standalone config (~/.config/zotero-mcp/config.json)."""
    try:
        from pathlib import Path
        cfg_path = Path.home() / ".config" / "zotero-mcp" / "config.json"
        if not cfg_path.exists():
            return {}
        with open(cfg_path) as f:
            cfg = json.load(f)
        return cfg.get("client_env", {}) or {}
    except Exception:
        return {}


def apply_environment_variables(env_vars):
    """Apply environment variables to current process."""
    for key, value in env_vars.items():
        if key not in os.environ:  # Don't override existing env vars
            os.environ[key] = str(value)


def _save_zotero_db_path_to_config(config_path: Path, db_path: str) -> None:
    """
    Save the Zotero database path to the configuration file.

    This allows users to specify --db-path once and have it remembered
    for subsequent runs without needing to specify it again.

    Args:
        config_path: Path to the configuration file
        db_path: Path to the Zotero database file
    """
    try:
        # Ensure config directory exists
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing config or create new one
        full_config = {}
        if config_path.exists():
            try:
                with open(config_path) as f:
                    full_config = json.load(f)
            except Exception:
                pass

        # Ensure semantic_search section exists
        if "semantic_search" not in full_config:
            full_config["semantic_search"] = {}

        # Save the db_path
        full_config["semantic_search"]["zotero_db_path"] = db_path

        # Write back to file
        with open(config_path, 'w') as f:
            json.dump(full_config, f, indent=2)

        console.print(f"[success]✓[/success] Saved Zotero database path to config: [blue]{config_path}[/blue]")

    except Exception as e:
        console.print(f"[warning]Warning: Could not save db_path to config: {e}[/warning]")


def setup_zotero_environment():
    """Setup Zotero environment for CLI commands."""
    # Load standalone env first so global flags (e.g., ZOTERO_NO_CLAUDE) take effect
    standalone_env_vars = load_standalone_env_vars()
    apply_environment_variables(standalone_env_vars)

    # Respect global switch to disable Claude detection
    no_claude = str(os.environ.get("ZOTERO_NO_CLAUDE", "")).lower() in ("1", "true", "yes")

    # Load and apply Claude Desktop env unless disabled
    if not no_claude:
        claude_env_vars = load_claude_desktop_env_vars()
        apply_environment_variables(claude_env_vars)

    # Apply fallback defaults for local Zotero if no config found
    fallback_env_vars = {
        "ZOTERO_LOCAL": "true",
        "ZOTERO_LIBRARY_ID": "0",
    }
    # Apply fallbacks only if not already set
    apply_environment_variables(fallback_env_vars)


def main():
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        description="Zotero Model Context Protocol server"
    )

    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Server command (default behavior)
    server_parser = subparsers.add_parser("serve", help="Run the MCP server")
    server_parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default="stdio",
        help="Transport to use (default: stdio)",
    )
    server_parser.add_argument(
        "--host",
        default="localhost",
        help="Host to bind to for SSE transport (default: localhost)",
    )
    server_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind to for SSE transport (default: 8000)",
    )

    # Setup command
    setup_parser = subparsers.add_parser("setup", help="Configure zotero-mcp (Claude Desktop or standalone)")
    setup_parser.add_argument("--no-local", action="store_true",
                             help="Configure for Zotero Web API instead of local API")
    setup_parser.add_argument("--api-key", help="Zotero API key (only needed with --no-local)")
    setup_parser.add_argument("--library-id", help="Zotero library ID (only needed with --no-local)")
    setup_parser.add_argument("--library-type", choices=["user", "group"], default="user",
                             help="Zotero library type (only needed with --no-local)")
    setup_parser.add_argument("--no-claude", action="store_true",
                             help="Skip Claude Desktop config; write standalone config for web-based clients")
    setup_parser.add_argument("--config-path", help="Path to Claude Desktop config file")
    setup_parser.add_argument("--skip-semantic-search", action="store_true",
                             help="Skip semantic search configuration")
    setup_parser.add_argument("--semantic-config-only", action="store_true",
                             help="Only configure semantic search, skip Zotero setup")

    # Update database command
    update_db_parser = subparsers.add_parser("update-db", help="Update semantic search database")
    update_db_parser.add_argument("--force-rebuild", action="store_true",
                                 help="Force complete rebuild of the database")
    update_db_parser.add_argument("--limit", type=int,
                                 help="Limit number of items to process (for testing)")
    update_db_parser.add_argument("--fulltext", action="store_true",
                                 help="Extract fulltext content from local Zotero database (slower but more comprehensive)")
    update_db_parser.add_argument("--config-path",
                                 help="Path to semantic search configuration file")
    update_db_parser.add_argument("--db-path",
                                 help="Path to Zotero database file (zotero.sqlite), overrides config")

    # Database status command
    db_status_parser = subparsers.add_parser("db-status", help="Show semantic search database status")
    db_status_parser.add_argument("--config-path",
                                 help="Path to semantic search configuration file")

    # DB inspect command (sample and filter indexed docs; also supports stats)
    inspect_parser = subparsers.add_parser("db-inspect", help="Inspect indexed documents or show aggregate stats for the semantic DB")
    inspect_parser.add_argument("--limit", type=int, default=20, help="How many records to show (default: 20)")
    inspect_parser.add_argument("--filter", dest="filter_text", help="Substring to match in title or creators")
    inspect_parser.add_argument("--show-documents", action="store_true", help="Show beginning of stored document text")
    inspect_parser.add_argument("--stats", action="store_true", help="Show aggregate stats (formerly db-stats)")
    inspect_parser.add_argument("--config-path", help="Path to semantic search configuration file")

    # Update command
    update_parser = subparsers.add_parser("update", help="Update zotero-mcp to the latest version")
    update_parser.add_argument("--check-only", action="store_true",
                              help="Only check for updates without installing")
    update_parser.add_argument("--force", action="store_true",
                              help="Force update even if already up to date")
    update_parser.add_argument("--method", choices=["pip", "uv", "conda", "pipx"],
                              help="Override auto-detected installation method")

    # Version command
    version_parser = subparsers.add_parser("version", help="Print version information")

    # Setup info command
    setup_info_parser = subparsers.add_parser("setup-info", help="Show installation path and configuration info for MCP clients")

    args = parser.parse_args()

    # If no command is provided, default to 'serve'
    if not args.command:
        args.command = "serve"
        # Also set default transport since we're defaulting to serve
        args.transport = "stdio"

    if args.command == "version":
        from zotero_mcp._version import __version__
        console.print(f"Zotero MCP [bold blue]v{__version__}[/bold blue]")
        sys.exit(0)

    elif args.command == "setup-info":
        # Setup Zotero environment variables
        setup_zotero_environment()

        # Get the installation path
        executable_path = shutil.which("zotero-mcp")
        if not executable_path:
            executable_path = sys.executable + " -m zotero_mcp"

        # Determine whether Claude is disabled globally
        no_claude = str(os.environ.get("ZOTERO_NO_CLAUDE", "")).lower() in ("1", "true", "yes")

        # Load current environment configurations
        standalone_env_vars = load_standalone_env_vars()
        claude_env_vars = {} if no_claude else load_claude_desktop_env_vars()

        # Choose which env to display: prefer standalone if present or if Claude disabled
        display_env = standalone_env_vars if (no_claude or standalone_env_vars) else (claude_env_vars or {"ZOTERO_LOCAL": "true"})

        console.print(Panel("[bold blue]Zotero MCP Setup Information[/bold blue]", expand=False))
        
        # Installation Details Table
        install_table = Table(title="🔧 Installation Details", show_header=False, box=None)
        install_table.add_row("Command path", f"[blue]{executable_path}[/blue]")
        install_table.add_row("Python path", f"[blue]{sys.executable}[/blue]")

        # Detect installation method
        method = "unknown"
        try:
            result = subprocess.run(["uv", "tool", "list"], capture_output=True, text=True, timeout=5)
            if "zotero-mcp-server" in result.stdout or "zotero-mcp" in result.stdout:
                method = "uv tool"
            else:
                result = subprocess.run([sys.executable, "-m", "pip", "show", "zotero-mcp-server"],
                                      capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    method = "pip"
        except Exception: pass
        install_table.add_row("Installation method", f"[blue]{method}[/blue]")
        console.print(install_table)

        # MCP Client Configuration
        console.print("\n[bold]⚙️  MCP Client Configuration:[/bold]")
        config_table = Table(show_header=False, box=None, padding=(0, 2))
        config_table.add_row("Command", f"[blue]{executable_path}[/blue]")
        config_table.add_row("Arguments", "[] (empty)")
        
        obfuscated_env_vars = obfuscate_config_for_display(display_env)
        config_table.add_row("Environment", f"[dim]{json.dumps(obfuscated_env_vars, separators=(',', ':'))}[/dim]")
        config_table.add_row("Claude integration", "[green]enabled[/green]" if not no_claude else "[red]disabled[/red]")
        console.print(config_table)

        # Only show Claude Desktop config if not globally disabled
        if not no_claude:
            console.print("\n[bold]For Claude Desktop (claude_desktop_config.json):[/bold]")
            config_snippet = {
                "mcpServers": {
                    "zotero": {
                        "command": executable_path,
                        "env": obfuscated_env_vars
                    }
                }
            }
            console.print(Panel(json.dumps(config_snippet, indent=2), border_style="dim"))

        # Semantic Search Info
        console.print("\n[bold]🧠 Semantic Search Database:[/bold]")
        config_path = Path.home() / ".config" / "zotero-mcp" / "config.json"
        if config_path.exists():
            try:
                from zotero_mcp.semantic_search import create_semantic_search
                from zotero_mcp.indexer import create_indexer

                search = create_semantic_search(str(config_path))
                status = search.get_database_status()
                index_status = create_indexer(str(config_path)).get_update_status()
                collection_info = status.get("collection_info", {})

                db_table = Table(show_header=False, box=None, padding=(0, 2))
                db_table.add_row("Status", "[success]✅ Configuration file found[/success]")
                db_table.add_row("Config path", f"[blue]{config_path}[/blue]")
                db_table.add_row("Collection", f"[blue]{collection_info.get('name', 'Unknown')}[/blue]")
                db_table.add_row("Document count", f"[blue]{collection_info.get('count', 0)}[/blue]")
                db_table.add_row("Embedding model", f"[blue]{collection_info.get('embedding_model', 'Unknown')}[/blue]")
                db_table.add_row("Database path", f"[blue]{collection_info.get('persist_directory', 'Unknown')}[/blue]")
                db_table.add_row("Retriever mode", f"[blue]{status.get('retriever_mode', 'legacy_metadata')}[/blue]")
                
                update_config = index_status.get("update_config", {})
                db_table.add_row("Auto update", f"[blue]{update_config.get('auto_update', False)}[/blue]")
                db_table.add_row("Update frequency", f"[blue]{update_config.get('update_frequency', 'manual')}[/blue]")
                db_table.add_row("Last update", f"[blue]{update_config.get('last_update', 'Never')}[/blue]")
                
                console.print(db_table)
            except Exception as e:
                console.print(f"  [error]⚠️ Configuration found but database error: {e}[/error]")
        else:
            console.print("  [warning]⚠️ Not configured[/warning]")
            console.print("  💡 Run [bold]zotero-mcp setup[/bold] to configure semantic search")

        sys.exit(0)

    elif args.command == "setup":
        from zotero_mcp.setup_helper import main as setup_main
        sys.exit(setup_main(args))

    elif args.command == "update-db":
        setup_zotero_environment()
        from zotero_mcp.indexer import create_indexer

        config_path = Path(args.config_path) if args.config_path else Path.home() / ".config" / "zotero-mcp" / "config.json"
        console.print(f"[info]Using configuration:[/info] [blue]{config_path}[/blue]")

        db_path = getattr(args, 'db_path', None)
        if db_path:
            console.print(f"[info]Using custom Zotero database:[/info] [blue]{db_path}[/blue]")
            _save_zotero_db_path_to_config(config_path, db_path)

        try:
            indexer = create_indexer(str(config_path), db_path=db_path)
            console.print("[bold]Starting database update...[/bold]")
            if args.fulltext:
                console.print("[dim]Note: --fulltext flag enabled. Will extract content from local database if available.[/dim]")
            
            stats = indexer.update_database(
                force_full_rebuild=args.force_rebuild,
                limit=args.limit,
                extract_fulltext=args.fulltext
            )
            
            retriever_mode = stats.get("retriever_mode", "legacy_metadata")
            added_count = stats.get("added_items", stats.get("added_chunks", 0))
            updated_count = stats.get("updated_items", stats.get("updated_chunks", 0))
            added_label = "Added chunks" if retriever_mode == "advanced_rag" else "Added"
            updated_label = "Updated chunks" if retriever_mode == "advanced_rag" else "Updated"

            console.print("\n[bold success]Database update completed:[/bold success]")
            summary_table = Table(show_header=False, box=None, padding=(0, 2))
            summary_table.add_row("Total items", str(stats.get('total_items', 0)))
            summary_table.add_row("Processed", str(stats.get('processed_items', 0)))
            summary_table.add_row(added_label, f"[green]{added_count}[/green]")
            summary_table.add_row(updated_label, f"[blue]{updated_count}[/blue]")
            summary_table.add_row("Skipped", str(stats.get('skipped_items', 0)))
            summary_table.add_row("Errors", f"[red]{stats.get('errors', 0)}[/red]")
            summary_table.add_row("Duration", str(stats.get('duration', 'Unknown')))
            console.print(summary_table)

            if stats.get('error'):
                console.print(f"[error]Error: {stats['error']}[/error]")
                sys.exit(1)

        except Exception as e:
            console.print(f"[error]Error updating database: {e}[/error]")
            sys.exit(1)

    elif args.command == "db-status":
        setup_zotero_environment()
        from zotero_mcp.semantic_search import create_semantic_search
        from zotero_mcp.indexer import create_indexer

        config_path = Path(args.config_path) if args.config_path else Path.home() / ".config" / "zotero-mcp" / "config.json"

        try:
            search = create_semantic_search(str(config_path))
            status = search.get_database_status()
            index_status = create_indexer(str(config_path)).get_update_status()

            console.print(Panel("[bold blue]Semantic Search Database Status[/bold blue]", expand=False))

            collection_info = status.get("collection_info", {})
            db_table = Table(show_header=False, box=None, padding=(0, 2))
            db_table.add_row("Collection", f"[blue]{collection_info.get('name', 'Unknown')}[/blue]")
            db_table.add_row("Document count", f"[blue]{collection_info.get('count', 0)}[/blue]")
            db_table.add_row("Embedding model", f"[blue]{collection_info.get('embedding_model', 'Unknown')}[/blue]")
            db_table.add_row("Database path", f"[blue]{collection_info.get('persist_directory', 'Unknown')}[/blue]")
            db_table.add_row("Retriever mode", f"[blue]{status.get('retriever_mode', 'legacy_metadata')}[/blue]")
            if status.get("reranker_status"):
                db_table.add_row("Reranker", f"[blue]{status.get('reranker_status')}[/blue]")
            console.print(db_table)

            update_config = index_status.get("update_config", {})
            console.print("\n[bold]Update configuration:[/bold]")
            up_table = Table(show_header=False, box=None, padding=(0, 2))
            up_table.add_row("Auto update", f"[blue]{update_config.get('auto_update', False)}[/blue]")
            up_table.add_row("Frequency", f"[blue]{update_config.get('update_frequency', 'manual')}[/blue]")
            up_table.add_row("Last update", f"[blue]{update_config.get('last_update', 'Never')}[/blue]")
            up_table.add_row("Should update", f"[blue]{index_status.get('should_update', False)}[/blue]")
            console.print(up_table)

            if collection_info.get('error'):
                console.print(f"\n[error]Error: {collection_info['error']}[/error]")

        except Exception as e:
            console.print(f"[error]Error getting database status: {e}[/error]")
            sys.exit(1)

    elif args.command == "db-inspect":
        setup_zotero_environment()
        from zotero_mcp.semantic_search import create_semantic_search
        from collections import Counter

        config_path = Path(args.config_path) if args.config_path else Path.home() / ".config" / "zotero-mcp" / "config.json"

        try:
            search = create_semantic_search(str(config_path))
            client = search.chroma_client
            col = client.collection

            if args.stats:
                meta = col.get(include=["metadatas"])  # type: ignore
                metas = meta.get("metadatas", [])
                console.print(Panel("[bold blue]Semantic DB Inspection (Stats)[/bold blue]", expand=False))
                
                info = client.get_collection_info()
                console.print(f"Collection: [blue]{info.get('name')}[/blue] @ [dim]{info.get('persist_directory')}[/dim]")
                console.print(f"Total Count: [bold blue]{info.get('count')}[/bold blue]")

                item_types = [ (m or {}).get("item_type", "") for m in metas ]
                ct_types = Counter(item_types)
                console.print("\n[bold]Item types:[/bold]")
                for t, c in ct_types.most_common(10):
                    console.print(f"  • {t or '(missing)'}: [blue]{c}[/blue]")

                return

            include = ["metadatas"]
            if args.show_documents: include.append("documents")
            data = col.get(limit=args.limit, include=include)

            console.print(Panel(f"[bold blue]Semantic DB Inspection[/bold blue] (showing {args.limit})", expand=False))

            shown = 0
            for i, meta in enumerate(data.get("metadatas", [])):
                meta = meta or {}
                title = meta.get("title", "Untitled")
                creators = meta.get("creators", "Unknown")
                if args.filter_text:
                    needle = args.filter_text.lower()
                    if needle not in title.lower() and needle not in creators.lower():
                        continue
                console.print(f"[bold]• {title}[/bold] | [dim]{creators}[/dim]")
                if args.show_documents:
                    doc = (data.get("documents", [""])[i] or "").strip()
                    snippet = doc[:150].replace("\n", " ") + ("..." if len(doc) > 150 else "")
                    if snippet: console.print(f"  [italic dim]{snippet}[/italic dim]")
                shown += 1
                if shown >= args.limit: break

            if shown == 0:
                console.print("[warning]No records matched your filter.[/warning]")

        except Exception as e:
            console.print(f"[error]Error inspecting database: {e}[/error]")
            sys.exit(1)

    elif args.command == "update":
        from zotero_mcp.updater import update_zotero_mcp
        try:
            console.print("[info]Checking for updates...[/info]")
            result = update_zotero_mcp(check_only=args.check_only, force=args.force, method=args.method)

            console.print(Panel("[bold blue]Update Results[/bold blue]", expand=False))

            if args.check_only:
                console.print(f"Current version: [blue]{result.get('current_version')}[/blue]")
                console.print(f"Latest version: [blue]{result.get('latest_version')}[/blue]")
                console.print(f"Update needed: {'[green]Yes[/green]' if result.get('needs_update') else '[dim]No[/dim]'}")
            else:
                if result.get('success'):
                    console.print("[success]✅ Update completed successfully![/success]")
                    console.print(f"Version: [blue]{result.get('current_version')}[/blue] → [bold green]{result.get('latest_version')}[/bold green]")
                else:
                    console.print(f"[error]❌ Update failed: {result.get('message')}[/error]")
                    sys.exit(1)
        except Exception as e:
            console.print(f"[error]❌ Update error: {e}[/error]")
            sys.exit(1)

    elif args.command == "serve":
        transport = getattr(args, "transport", "stdio")
        setup_zotero_environment()
        if transport == "stdio":
            mcp.run(transport="stdio")
        elif transport == "streamable-http":
            mcp.run(transport="streamable-http", host=getattr(args, "host", "localhost"), port=getattr(args, "port", 8000))
        elif transport == "sse":
            import warnings
            warnings.warn("SSE transport is deprecated.", UserWarning)
            mcp.run(transport="sse", host=getattr(args, "host", "localhost"), port=getattr(args, "port", 8000))


if __name__ == "__main__":
    main()
