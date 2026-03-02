#!/usr/bin/env python

"""
Setup helper for zotero-mcp.

This script provides utilities to automatically configure zotero-mcp
by finding the installed executable and updating Claude Desktop's config.
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import questionary
from questionary import Style
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()

# Define custom style for questionary to match the requested UI
custom_style = Style([
    ('qmark', 'fg:#00d700 bold'),       # Question mark color (green)
    ('question', 'bold'),               # Question text
    ('answer', 'fg:#00d700 bold'),      # Answer text
    ('pointer', 'fg:#00d700 bold'),     # Pointer (green)
    ('highlighted', 'fg:#00d700 bold noinherit'), # Force no background on highlight
    ('selected', 'fg:#00d700 noinherit'),         # Force no background on selected
    ('separator', 'fg:#cc5454'),        # Separator
    ('instruction', 'fg:#8a8a8a'),      # Instruction text
    ('text', ''),                       # Plain text
])

class SetupUI:
    """Helper class to handle the interactive setup UI."""
    
    def __init__(self):
        self.steps = []
        self.current_step_idx = -1

    def _handle_cancel(self, result):
        if result is None:
            console.print("\n[yellow]Setup cancelled by user.[/yellow]")
            sys.exit(0)
        return result

    def add_step(self, name):
        self.steps.append(name)

    def start_step(self, name):
        if name in self.steps:
            self.current_step_idx = self.steps.index(name)
        else:
            self.steps.append(name)
            self.current_step_idx = len(self.steps) - 1
        
        self._print_step_header()

    def _print_step_header(self):
        # console.clear() # Optional: clear screen for each step
        print()
        for i, step in enumerate(self.steps):
            if i < self.current_step_idx:
                # Completed step: aligns with the left edge of the diamond
                console.print(f"  [dim]✔ {step}[/dim]")
            elif i == self.current_step_idx:
                # Current step: using 3:2 space ratio to align the line with the diamond's center/right
                # In most CJK terminals, ◆ is 2-cells wide.
                # 3 spaces + │ (1-cell) = 4 cells total
                # 2 spaces + ◆ (2-cells) = 4 cells total
                # This aligns the vertical line with the right half of the diamond.
                console.print(f"   [bold blue]│[/bold blue]")
                console.print(f"  [bold blue]◆[/bold blue] [bold]{step}[/bold]")
                console.print(f"   [bold blue]│[/bold blue]")
            else:
                # Future step
                pass

    def ask_select(self, message, choices, default=None):
        result = questionary.select(
            message,
            choices=choices,
            default=default if default is not None else choices[0] if isinstance(choices[0], str) else choices[0].value,
            style=custom_style,
            use_indicator=True,
            pointer='❯'
        ).ask()
        return self._handle_cancel(result)

    def ask_text(self, message, default="", instruction=None):
        result = questionary.text(
            message,
            default=str(default) if default is not None else "",
            instruction=instruction,
            style=custom_style
        ).ask()
        return self._handle_cancel(result)

    def ask_password(self, message, instruction=None):
        result = questionary.password(
            message,
            instruction=instruction,
            style=custom_style
        ).ask()
        return self._handle_cancel(result)

    def ask_confirm(self, message, default=True):
        result = questionary.confirm(
            message,
            default=default,
            style=custom_style
        ).ask()
        return self._handle_cancel(result)


ui = SetupUI()


def _obfuscate_sensitive(value: str | None, keep_chars: int = 4) -> str:
    """Obfuscate sensitive values for terminal display."""
    if not value:
        return "Not provided"
    if len(value) <= keep_chars:
        return "*" * len(value)
    return value[:keep_chars] + "*" * (len(value) - keep_chars)


def find_executable():
    """Find the full path to the zotero-mcp executable."""
    # Try to find the executable in the PATH
    exe_name = "zotero-mcp"
    if sys.platform == "win32":
        exe_name += ".exe"

    exe_path = shutil.which(exe_name)
    if exe_path:
        return exe_path

    # If not found in PATH, try to find it in common installation directories
    potential_paths = []

    # User site-packages
    import site
    try:
        for site_path in site.getsitepackages():
            potential_paths.append(Path(site_path) / "bin" / exe_name)
    except AttributeError:
        # Some environments might not have getsitepackages
        pass

    # User's home directory
    potential_paths.append(Path.home() / ".local" / "bin" / exe_name)

    # Virtual environment
    if "VIRTUAL_ENV" in os.environ:
        potential_paths.append(Path(os.environ["VIRTUAL_ENV"]) / "bin" / exe_name)

    # Additional common locations
    if sys.platform == "darwin":  # macOS
        potential_paths.append(Path("/usr/local/bin") / exe_name)
        potential_paths.append(Path("/opt/homebrew/bin") / exe_name)

    for path in potential_paths:
        if path.exists() and os.access(path, os.X_OK):
            return str(path)

    # If still not found, search in common directories
    try:
        # On Unix-like systems, try using the 'find' command
        if sys.platform != 'win32':
            import subprocess
            result = subprocess.run(
                ["find", os.path.expanduser("~"), "-name", "zotero-mcp", "-type", "f", "-executable"],
                capture_output=True, text=True, timeout=5
            )
            paths = result.stdout.strip().split('\n')
            if paths and paths[0]:
                return paths[0]
    except Exception:
        pass

    return None


def find_claude_config():
    """Find Claude Desktop config file path."""
    config_paths = []

    # macOS
    if sys.platform == "darwin":
        # Try both old and new paths
        config_paths.append(Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
        config_paths.append(Path.home() / "Library" / "Application Support" / "Claude Desktop" / "claude_desktop_config.json")

    # Windows
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            config_paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
            config_paths.append(Path(appdata) / "Claude Desktop" / "claude_desktop_config.json")

    # Linux
    else:
        config_home = os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')
        config_paths.append(Path(config_home) / "Claude" / "claude_desktop_config.json")
        config_paths.append(Path(config_home) / "Claude Desktop" / "claude_desktop_config.json")

    # Check all possible locations
    for path in config_paths:
        if path.exists():
            return path

    # Return the default path for the platform if not found
    if sys.platform == "darwin":  # macOS
        default_path = Path.home() / "Library" / "Application Support" / "Claude Desktop" / "claude_desktop_config.json"
    elif sys.platform == "win32":  # Windows
        appdata = os.environ.get("APPDATA", "")
        default_path = Path(appdata) / "Claude Desktop" / "claude_desktop_config.json"
    else:  # Linux and others
        config_home = os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')
        default_path = Path(config_home) / "Claude Desktop" / "claude_desktop_config.json"

    return default_path

def setup_semantic_search(existing_semantic_config: dict = None, semantic_config_only_arg: bool = False) -> dict:
    """Interactive setup for semantic search configuration."""
    ui.start_step("Semantic Search Configuration")
    existing_semantic_config = existing_semantic_config or {}

    if existing_semantic_config:
        # Display config without sensitive info
        model = existing_semantic_config.get("embedding_model", "unknown")
        name = existing_semantic_config.get("embedding_config", {}).get("model_name", "unknown")
        update_freq = existing_semantic_config.get("update_config", {}).get("update_frequency", "unknown")
        db_path = existing_semantic_config.get("zotero_db_path", "auto-detect")
        retriever_mode = existing_semantic_config.get("retriever_mode", "legacy_metadata")
        
        console.print(Panel(
            f"[bold]Current Configuration:[/bold]\n"
            f"  • Embedding model: [blue]{model}[/blue]\n"
            f"  • Model name: [blue]{name}[/blue]\n"
            f"  • Retriever mode: [blue]{retriever_mode}[/blue]\n"
            f"  • Update frequency: [blue]{update_freq}[/blue]\n"
            f"  • Zotero DB path: [blue]{db_path}[/blue]",
            title="Existing Settings",
            border_style="dim"
        ))
        
        if ui.ask_confirm("Would you like to keep your existing configuration?", default=True):
            return existing_semantic_config

    ui.start_step("Select Embedding Model")
    choice = ui.ask_select(
        "Choose embedding model for semantic search:",
        choices=[
            questionary.Choice("Default (all-MiniLM-L6-v2) - Free, runs locally", "1"),
            questionary.Choice("OpenAI - Better quality, requires API key", "2"),
            questionary.Choice("Gemini - Better quality, requires API key", "3"),
        ]
    )

    config = {}

    if choice == "1":
        config["embedding_model"] = "default"
        console.print("  [green]✓[/green] Using default embedding model (all-MiniLM-L6-v2)")

    elif choice == "2":
        config["embedding_model"] = "openai"
        model_choice = ui.ask_select(
            "Choose OpenAI model:",
            choices=[
                questionary.Choice("text-embedding-3-small (recommended, faster)", "1"),
                questionary.Choice("text-embedding-3-large (higher quality, slower)", "2"),
            ]
        )

        if model_choice == "1":
            config["embedding_config"] = {"model_name": "text-embedding-3-small"}
        else:
            config["embedding_config"] = {"model_name": "text-embedding-3-large"}

        api_key = ui.ask_password("Enter your OpenAI API key:")
        if api_key:
            config["embedding_config"]["api_key"] = api_key
        else:
            console.print("  [yellow]![/yellow] Warning: No API key provided. Set OPENAI_API_KEY environment variable.")

        base_url = ui.ask_text("Enter custom OpenAI base URL (leave blank for default):")
        if base_url:
            config["embedding_config"]["base_url"] = base_url

    elif choice == "3":
        config["embedding_model"] = "gemini"
        config["embedding_config"] = {"model_name": "gemini-embedding-001"}

        api_key = ui.ask_password("Enter your Gemini API key:")
        if api_key:
            config["embedding_config"]["api_key"] = api_key
        else:
            console.print("  [yellow]![/yellow] Warning: No API key provided. Set GEMINI_API_KEY environment variable.")

        base_url = ui.ask_text("Enter custom Gemini base URL (leave blank for default):")
        if base_url:
            config["embedding_config"]["base_url"] = base_url

    ui.start_step("Retrieval Mode")
    existing_mode = existing_semantic_config.get("retriever_mode", "legacy_metadata")
    mode_map = {
        "legacy_metadata - metadata-only semantic indexing": "legacy_metadata",
        "legacy_fulltext - legacy mode with optional --fulltext extraction": "legacy_fulltext",
        "advanced_rag - chunked retrieval using MinerU markdown (md_root)": "advanced_rag"
    }
    
    default_mode_key = next((k for k, v in mode_map.items() if v == existing_mode), list(mode_map.keys())[0])
    
    mode_selection = ui.ask_select(
        "Choose how semantic indexing and retrieval should work:",
        choices=list(mode_map.keys()),
        default=default_mode_key
    )
    
    retriever_mode = mode_map[mode_selection]
    config["retriever_mode"] = retriever_mode

    if retriever_mode == "advanced_rag":
        ui.start_step("Advanced RAG Settings")
        existing_advanced = existing_semantic_config.get("advanced_rag", {})
        default_md_root = existing_advanced.get(
            "md_root",
            str(Path.home() / ".config" / "zotero-mcp" / "md_root")
        )
        md_root = ui.ask_text("MinerU markdown root:", default=default_md_root)
        
        if not Path(md_root).exists():
            console.print(f"  [yellow]![/yellow] Warning: md_root does not exist: {md_root}")

        backend_choice = ui.ask_select(
            "Choose chunking backend:",
            choices=[
                questionary.Choice("langchain (markdown_recursive_v1) - recommended for English papers", "1"),
                questionary.Choice("legacy - original character sliding-window", "2"),
            ],
            default="1" if existing_advanced.get("chunk", {}).get("backend", "langchain") == "langchain" else "2"
        )
        chunk_backend = "langchain" if backend_choice == "1" else "legacy"

        if chunk_backend == "langchain":
            strategy_choice = ui.ask_select(
                "Choose chunking strategy:",
                choices=[
                    questionary.Choice("markdown_recursive_v1 - stable default", "1"),
                    questionary.Choice("semantic_v1 - experimental", "2"),
                ],
                default="1" if existing_advanced.get("chunk", {}).get("strategy", "markdown_recursive_v1") == "markdown_recursive_v1" else "2"
            )
            chunk_strategy = "semantic_v1" if strategy_choice == "2" else "markdown_recursive_v1"

            chunk_size = int(ui.ask_text("chunk_size:", default=str(existing_advanced.get("chunk", {}).get("chunk_size", 1100))))
            chunk_overlap = int(ui.ask_text("chunk_overlap:", default=str(existing_advanced.get("chunk", {}).get("chunk_overlap", 180))))

            chunk_cfg = {
                "backend": "langchain",
                "strategy": chunk_strategy,
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
                "min_chunk_chars": existing_advanced.get("chunk", {}).get("min_chunk_chars", 220),
            }
        else:
            chunk_cfg = {
                "backend": "legacy",
                "max_chars": existing_advanced.get("chunk", {}).get("max_chars", 1600),
                "overlap_chars": existing_advanced.get("chunk", {}).get("overlap_chars", 200),
                "min_chunk_chars": existing_advanced.get("chunk", {}).get("min_chunk_chars", 120),
                "heading_first": existing_advanced.get("chunk", {}).get("heading_first", True),
            }

        reranker_enabled = ui.ask_confirm(
            "Enable reranker (flashrank)?",
            default=existing_advanced.get("reranker", {}).get("enabled", True),
        )
        
        reranker_model = ui.ask_text(
            "Reranker model:",
            default=existing_advanced.get("reranker", {}).get("model_name", "ms-marco-MiniLM-L-12-v2")
        )
        
        reranker_top_n = int(ui.ask_text(
            "Reranker top_n:",
            default=str(existing_advanced.get("reranker", {}).get("top_n", 8))
        ))

        reranker_cache = ui.ask_text(
            "Reranker cache dir (leave blank for default):",
            default=existing_advanced.get("reranker", {}).get("local_model_path", "")
        )

        config["advanced_rag"] = {
            "md_root": md_root,
            "chunk": chunk_cfg,
            "ingest": {
                "strip_images": existing_advanced.get("ingest", {}).get("strip_images", True),
            },
            "reranker": {
                "enabled": reranker_enabled,
                "backend": "flashrank",
                "model_name": reranker_model,
                "local_model_path": reranker_cache or None,
                "top_n": reranker_top_n,
            },
            "retrieve": {
                "candidate_k": existing_advanced.get("retrieve", {}).get("candidate_k", 30),
                "evidence_per_item": existing_advanced.get("retrieve", {}).get("evidence_per_item", 2),
                "meta_weight": existing_advanced.get("retrieve", {}).get("meta_weight", 0.70),
            },
        }

    ui.start_step("Database Update Configuration")
    update_choice = ui.ask_select(
        "Choose update frequency:",
        choices=[
            questionary.Choice("Manual - Update only when you run 'zotero-mcp update-db'", "1"),
            questionary.Choice("Auto - Automatically update on server startup", "2"),
            questionary.Choice("Daily - Automatically update once per day", "3"),
            questionary.Choice("Every N days - Automatically update every N days", "4"),
        ]
    )

    update_config = {}
    if update_choice == "1":
        update_config = {"auto_update": False, "update_frequency": "manual"}
    elif update_choice == "2":
        update_config = {"auto_update": True, "update_frequency": "startup"}
    elif update_choice == "3":
        update_config = {"auto_update": True, "update_frequency": "daily"}
    elif update_choice == "4":
        days = int(ui.ask_text("Enter number of days between updates:", default="7"))
        update_config = {
            "auto_update": True,
            "update_frequency": f"every_{days}",
            "update_days": days
        }

    if retriever_mode in {"legacy_metadata", "legacy_fulltext"}:
        ui.start_step("Content Extraction Settings")
        default_pdf_max = existing_semantic_config.get("extraction", {}).get("pdf_max_pages", 10)
        pdf_max_pages = int(ui.ask_text("PDF max pages for extraction:", default=str(default_pdf_max)))
        config["extraction"] = {"pdf_max_pages": pdf_max_pages}

    ui.start_step("Zotero Database Path")
    default_db_path = existing_semantic_config.get("zotero_db_path", "")
    db_path_hint = default_db_path if default_db_path else "auto-detect"
    raw_db_path = ui.ask_text("Zotero database path (leave blank for auto-detect):", default=default_db_path)

    zotero_db_path = None
    if raw_db_path:
        db_file = Path(raw_db_path)
        if db_file.exists() and db_file.is_file():
            zotero_db_path = str(db_file)
        else:
            console.print(f"  [yellow]![/yellow] Warning: File not found at '{raw_db_path}'. Using auto-detect.")
    
    config["update_config"] = update_config
    if zotero_db_path:
        config["zotero_db_path"] = zotero_db_path

    return config


def save_semantic_search_config(config: dict, semantic_config_path: Path) -> bool:
    """Save semantic search configuration to file."""
    try:
        semantic_config_dir = semantic_config_path.parent
        semantic_config_dir.mkdir(parents=True, exist_ok=True)

        full_semantic_config = {}
        if semantic_config_path.exists():
            try:
                with open(semantic_config_path) as f:
                    full_semantic_config = json.load(f)
            except json.JSONDecodeError:
                pass

        full_semantic_config["semantic_search"] = config

        with open(semantic_config_path, 'w') as f:
            json.dump(full_semantic_config, f, indent=2)

        return True
    except Exception as e:
        console.print(f"[red]Error saving semantic search config: {e}[/red]")
        return False

def load_semantic_search_config(semantic_config_path: Path) -> dict:
    """Load existing semantic search configuration."""
    if not semantic_config_path.exists():
        return {}

    try:
        with open(semantic_config_path) as f:
            full_semantic_config = json.load(f)
        return full_semantic_config.get("semantic_search", {})
    except Exception:
        return {}


def update_claude_config(config_path, zotero_mcp_path, local=True, api_key=None, library_id=None, library_type="user", semantic_config=None):
    """Update Claude Desktop config to add zotero-mcp."""
    config_dir = config_path.parent
    config_dir.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        try:
            with open(config_path) as f:
                config = json.load(f)
        except json.JSONDecodeError:
            config = {}
    else:
        config = {}

    if "mcpServers" not in config:
        config["mcpServers"] = {}

    env_settings = {"ZOTERO_LOCAL": "true" if local else "false"}

    if not local:
        if api_key: env_settings["ZOTERO_API_KEY"] = api_key
        if library_id: env_settings["ZOTERO_LIBRARY_ID"] = library_id
        if library_type: env_settings["ZOTERO_LIBRARY_TYPE"] = library_type

    if semantic_config:
        env_settings["ZOTERO_EMBEDDING_MODEL"] = semantic_config.get("embedding_model", "default")
        embedding_config = semantic_config.get("embedding_config", {})
        
        if semantic_config.get("embedding_model") == "openai":
            if ak := embedding_config.get("api_key"): env_settings["OPENAI_API_KEY"] = ak
            if md := embedding_config.get("model_name"): env_settings["OPENAI_EMBEDDING_MODEL"] = md
            if bu := embedding_config.get("base_url"): env_settings["OPENAI_BASE_URL"] = bu
        elif semantic_config.get("embedding_model") == "gemini":
            if ak := embedding_config.get("api_key"): env_settings["GEMINI_API_KEY"] = ak
            if md := embedding_config.get("model_name"): env_settings["GEMINI_EMBEDDING_MODEL"] = md
            if bu := embedding_config.get("base_url"): env_settings["GEMINI_BASE_URL"] = bu

    config["mcpServers"]["zotero"] = {
        "command": zotero_mcp_path,
        "env": env_settings
    }

    try:
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)
        return config_path
    except Exception as e:
        console.print(f"[red]Error writing config file: {str(e)}[/red]")
        return False


def _write_standalone_config(local: bool, api_key: str, library_id: str, library_type: str, semantic_config: dict, no_claude: bool = False) -> Path:
    """Write a central config file used by semantic search and provide client env."""
    cfg_dir = Path.home() / ".config" / "zotero-mcp"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "config.json"

    full = {}
    if cfg_path.exists():
        try:
            with open(cfg_path) as f:
                full = json.load(f)
        except Exception:
            full = {}

    if semantic_config:
        full["semantic_search"] = semantic_config

    client_env = {"ZOTERO_LOCAL": "true" if local else "false"}
    if no_claude: client_env["ZOTERO_NO_CLAUDE"] = "true"
    if not local:
        if api_key: client_env["ZOTERO_API_KEY"] = api_key
        if library_id: client_env["ZOTERO_LIBRARY_ID"] = library_id
        if library_type: client_env["ZOTERO_LIBRARY_TYPE"] = library_type

    full["client_env"] = client_env

    with open(cfg_path, 'w') as f:
        json.dump(full, f, indent=2)

    return cfg_path


def main(cli_args=None):
    """Main function to run the setup helper."""
    console.print(Panel.fit(
        "[bold blue]Zotero MCP Setup[/bold blue]\n"
        "Configure your Zotero Model Context Protocol server",
        border_style="blue"
    ))

    parser = argparse.ArgumentParser(description="Configure zotero-mcp for Claude Desktop")
    parser.add_argument("--no-local", action="store_true")
    parser.add_argument("--no-claude", action="store_true")
    parser.add_argument("--api-key")
    parser.add_argument("--library-id")
    parser.add_argument("--library-type", choices=["user", "group"], default="user")
    parser.add_argument("--config-path")
    parser.add_argument("--skip-semantic-search", action="store_true")
    parser.add_argument("--semantic-config-only", action="store_true")

    if cli_args is not None and hasattr(cli_args, 'no_local'):
        args = cli_args
    else:
        args = parser.parse_args()

    semantic_config_dir = Path.home() / ".config" / "zotero-mcp"
    semantic_config_path = semantic_config_dir / "config.json"
    existing_semantic_config = load_semantic_search_config(semantic_config_path)
    semantic_config_changed = False

    if args.semantic_config_only:
        new_semantic_config = setup_semantic_search(existing_semantic_config)
        semantic_config_changed = existing_semantic_config != new_semantic_config
        if semantic_config_changed:
            if save_semantic_search_config(new_semantic_config, semantic_config_path):
                console.print("\n[green]✓[/green] Semantic search configuration complete!")
                console.print(f"  Configuration saved to: [blue]{semantic_config_path}[/blue]")
                console.print("\n  To initialize the database, run: [bold]zotero-mcp update-db[/bold]")
                return 0
            else:
                return 1
        else:
            console.print("\n[dim]Semantic search configuration left unchanged.[/dim]")
            return 0

    exe_path = find_executable()
    if not exe_path:
        console.print("[red]Error: Could not find zotero-mcp executable.[/red]")
        return 1
    
    console.print(f"  [green]✓[/green] Found zotero-mcp at: [blue]{exe_path}[/blue]")

    config_path = None
    if not args.no_claude:
        config_path = args.config_path
        if not config_path:
            config_path = find_claude_config()
        else:
            config_path = Path(config_path)
        
        if not config_path:
            console.print("[red]Error: Could not determine Claude Desktop config path.[/red]")
            return 1
        console.print(f"  [green]✓[/green] Using Claude config at: [blue]{config_path}[/blue]")

    use_local = not args.no_local
    api_key = args.api_key
    library_id = args.library_id
    library_type = args.library_type

    if not args.skip_semantic_search:
        prompt_msg = "Reconfigure semantic search?" if existing_semantic_config else "Configure semantic search?"
        if ui.ask_confirm(prompt_msg, default=True):
            new_semantic_config = setup_semantic_search(existing_semantic_config)
            if existing_semantic_config != new_semantic_config:
                semantic_config_changed = True
                existing_semantic_config = new_semantic_config
                save_semantic_search_config(existing_semantic_config, semantic_config_path)

    ui.start_step("Finalizing Setup")
    semantic_config = existing_semantic_config

    try:
        if args.no_claude:
            cfg_path = _write_standalone_config(
                local=use_local, api_key=api_key, library_id=library_id,
                library_type=library_type, semantic_config=semantic_config,
                no_claude=args.no_claude
            )
            console.print("\n[bold green]Setup complete (standalone/web mode)![/bold green]")
            console.print(f"Config saved to: [blue]{cfg_path}[/blue]")
            
            try:
                with open(cfg_path) as f:
                    full = json.load(f)
                env_line = json.dumps(full.get("client_env", {}), separators=(',', ':'))
                console.print("\n[bold]Client environment (single-line JSON):[/bold]")
                console.print(f"  {env_line}")
            except Exception: pass
            
            if semantic_config:
                mode = semantic_config.get("retriever_mode", "legacy_metadata")
                console.print(f"\n[bold]Semantic Search:[/bold]")
                console.print(f"• Model: [blue]{semantic_config.get('embedding_model', 'default')}[/blue]")
                console.print(f"• Mode: [blue]{mode}[/blue]")
                console.print("\n[bold]Database Indexing:[/bold]")
                console.print("• Run [bold]zotero-mcp update-db[/bold] to incrementally index new or changed items.")
                console.print("• Run [bold]zotero-mcp update-db --force-rebuild[/bold] to completely rebuild your index.")
                console.print("  [dim](Recommended if you changed embedding models or retrieval modes)[/dim]")
            return 0
        else:
            updated_config_path = update_claude_config(
                config_path, exe_path, local=use_local, api_key=api_key,
                library_id=library_id, library_type=library_type,
                semantic_config=semantic_config
            )
            if updated_config_path:
                console.print("\n[bold green]Setup complete![/bold green]")
                
                if semantic_config:
                    mode = semantic_config.get("retriever_mode", "legacy_metadata")
                    console.print(f"\n[bold]Semantic Search:[/bold]")
                    console.print(f"• Model: [blue]{semantic_config.get('embedding_model', 'default')}[/blue]")
                    console.print(f"• Mode: [blue]{mode}[/blue]")
                    console.print("\n[bold]Database Indexing:[/bold]")
                    console.print("• Run [bold]zotero-mcp update-db[/bold] to incrementally index new or changed items.")
                    console.print("• Run [bold]zotero-mcp update-db --force-rebuild[/bold] to completely rebuild your index.")
                    console.print("  [dim](Recommended if you changed embedding models or retrieval modes)[/dim]")
                
                if use_local:
                    console.print("\n[dim]Note: Make sure Zotero is running and the local API is enabled.[/dim]")
                return 0
            else:
                return 1
    except Exception as e:
        console.print(f"\n[red]Setup failed with error: {str(e)}[/red]")
        return 1


if __name__ == "__main__":
    sys.exit(main())
