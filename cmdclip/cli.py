"""
cli.py — cmdclip command-line interface
Built with Typer + Rich for cross-platform terminal UI.
"""

import importlib.metadata
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich import print as rprint

from cmdclip import storage, ai, platform_utils
from cmdclip.templates import is_template, resolve_template, preview_template


def version_callback(value: bool):
    if value:
        ver = importlib.metadata.version("cmdclip")
        plat = platform_utils.get_platform_name()
        if plat == "termux":
            plat_str = "termux (Android)"
        else:
            plat_str = plat
        backend = platform_utils.get_clipboard_backend()
        py_ver = sys.version.split()[0]
        console.print(
            f"cmdclip {ver}\n"
            f"Platform: {plat_str}\n"
            f"Clipboard backend: {backend}\n"
            f"Python: {py_ver}"
        )
        raise typer.Exit()


app = typer.Typer(
    name="cmdclip",
    help="📋 A smart cross-platform command clipboard manager.",
    add_completion=False,
)
config_app = typer.Typer(help="Manage cmdclip configuration.")
app.add_typer(config_app, name="config")

console = Console()


@app.callback()
def main_callback(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-V",
        help="Show version and system info.",
        callback=version_callback,
        is_eager=True,
    ),
):
    pass


# ─── Helpers ──────────────────────────────────────────────────────────────────

DANGEROUS_PATTERNS = ["rm -rf", "sudo rm", "DROP TABLE", "mkfs", ":(){:|:&};:"]


def _is_dangerous(cmd: str) -> bool:
    return any(p in cmd for p in DANGEROUS_PATTERNS)


def _copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard. Returns True on success."""
    return platform_utils.copy_to_clipboard(text)


def _paste_from_clipboard() -> Optional[str]:
    """Paste text from system clipboard. Returns string or None."""
    return platform_utils.paste_from_clipboard()


def _run_command(cmd: str) -> None:
    """Execute a shell command on any platform."""
    if platform.system() == "Windows":
        subprocess.run(cmd, shell=True)
    else:
        subprocess.run(cmd, shell=True, executable="/bin/bash")


def _print_command_table(commands: list[dict], title: str = "Commands") -> None:
    if not commands:
        console.print("[yellow]No commands found.[/yellow]")
        return

    table = Table(title=title, show_lines=True, header_style="bold cyan")
    table.add_column("ID", style="dim", width=10)
    table.add_column("Name", style="bold", width=16)
    table.add_column("Command", width=38)
    table.add_column("Tags", style="cyan", width=18)
    table.add_column("Uses", justify="right", width=5)

    for c in commands:
        tags = ", ".join(c.get("tags", []))
        name = c.get("name") or "-"
        uses = str(c.get("use_count", 0))
        cmd_preview = c["cmd"]
        if len(cmd_preview) > 55:
            cmd_preview = cmd_preview[:52] + "..."
        table.add_row(c["id"], name, cmd_preview, tags, uses)

    console.print(table)


# ─── Commands ─────────────────────────────────────────────────────────────────

@app.command()
def add(
    cmd: str = typer.Argument(..., help="The shell command to save."),
    tags: Optional[str] = typer.Option(None, "--tags", "-t", help="Comma-separated tags."),
    note: str = typer.Option("", "--note", "-n", help="Short description."),
    name: str = typer.Option("", "--name", help="Optional short name/alias."),
    no_ai: bool = typer.Option(False, "--no-ai", help="Skip AI tag suggestions."),
):
    """Add a new command to your clipboard."""
    tag_list: list[str] = []

    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    elif not no_ai:
        with console.status("[cyan]Suggesting tags via AI...[/cyan]"):
            suggested = ai.suggest_tags(cmd)
        if suggested:
            console.print(f"[cyan]Suggested tags:[/cyan] {', '.join(suggested)}")
            accept = Confirm.ask("Accept these tags?", default=True)
            if accept:
                tag_list = suggested
            else:
                raw = Prompt.ask("Enter tags manually (comma-separated)")
                tag_list = [t.strip() for t in raw.split(",") if t.strip()]

    template = is_template(cmd)
    entry = storage.add_command(cmd, tag_list, note=note, name=name, template=template)

    console.print(Panel(
        f"[bold green]✓ Saved![/bold green]\n"
        f"ID:   [yellow]{entry['id']}[/yellow]\n"
        f"Cmd:  {entry['cmd']}\n"
        f"Tags: {', '.join(entry['tags']) or 'none'}"
        + (f"\nNote: {note}" if note else "")
        + ("\n[cyan]📝 Template detected — variables will be filled on run.[/cyan]" if template else ""),
        title="cmdclip",
        border_style="green",
    ))


@app.command("list")
def list_commands(
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Filter by tag."),
    query: str = typer.Option("", "--query", "-q", help="Search keyword."),
):
    """List all saved commands."""
    results = storage.search_commands(query=query, tag=tag)
    title = "All Commands"
    if tag:
        title += f" [tag: {tag}]"
    if query:
        title += f" [search: {query}]"
    _print_command_table(results, title=title)


@app.command()
def search(
    query: str = typer.Argument(..., help="Search term."),
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Filter by tag."),
):
    """Search commands by keyword, tag, or note."""
    results = storage.search_commands(query=query, tag=tag)
    _print_command_table(results, title=f'Search: "{query}"')


@app.command()
def run(
    id_: str = typer.Argument(..., metavar="ID", help="Command ID to run."),
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="Preview only, don't execute."),
    exec_: bool = typer.Option(False, "--exec", "-e", help="Execute the command instead of copying."),
):
    """Run a saved command by ID."""
    entry = storage.get_command_by_id(id_)
    if not entry:
        console.print(f"[red]No command found with ID: {id_}[/red]")
        raise typer.Exit(1)

    cmd = entry["cmd"]

    # Resolve template variables
    if entry.get("template") or is_template(cmd):
        console.print(Panel(preview_template(cmd), title="Template", border_style="cyan"))
        console.print("[cyan]Fill in the variables:[/cyan]")
        try:
            cmd = resolve_template(cmd)
        except KeyboardInterrupt:
            console.print("\n[yellow]Cancelled.[/yellow]")
            raise typer.Exit()

    # Dry-run: show explanation and stop
    if dry_run:
        console.print(Panel(
            f"[bold]Command:[/bold] {cmd}\n\n"
            + ("[bold yellow]⚠ Contains dangerous pattern![/bold yellow]\n\n" if _is_dangerous(cmd) else "")
            + "[bold]Explanation:[/bold]\n" + ai.explain_command(cmd),
            title="Dry Run Preview",
            border_style="yellow",
        ))
        return

    # Dangerous check
    if _is_dangerous(cmd):
        console.print(f"[bold red]⚠ WARNING: This command contains a dangerous pattern![/bold red]")
        console.print(f"[dim]{cmd}[/dim]")
        if not Confirm.ask("[red]Are you sure you want to run it?[/red]", default=False):
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit()

    if exec_:
        console.print(f"[dim]$ {cmd}[/dim]")
        storage.increment_use_count(id_)
        _run_command(cmd)
    else:
        if _copy_to_clipboard(cmd):
            console.print(f"[green]✓ Copied to clipboard:[/green] {cmd}")
        else:
            console.print(f"[yellow]Could not access clipboard. Command:[/yellow]\n{cmd}")


@app.command("termux-setup")
def termux_setup():
    """Setup shell aliases and integration for Termux."""
    if not platform_utils.is_termux():
        console.print("[red]Error: termux-setup is only supported on Android Termux.[/red]")
        raise typer.Exit(1)

    shell = os.environ.get("SHELL", "")
    rc_file = ".zshrc" if "zsh" in shell else ".bashrc"
    rc_path = Path.home() / rc_file

    snippet = (
        "\n# cmdclip shell integration (Termux)\n"
        "alias cc='cmdclip run --exec'\n"
        "alias ca='cmdclip add'\n"
        "alias cs='cmdclip search'\n\n"
        "# Auto-save last command to cmdclip\n"
        "# Add this to your .bashrc:\n"
        "# PROMPT_COMMAND='cmdclip add \"$(history 1 | sed \"s/^[ ]*[0-9]*[ ]*//\")\" --no-ai --name \"auto\" 2>/dev/null || true'\n"
    )

    console.print(Panel(snippet, title="Termux Shell Integration", border_style="cyan"))

    if Confirm.ask(f"Do you want to append these aliases to [cyan]~/{rc_file}[/cyan]?", default=True):
        with open(rc_path, "a", encoding="utf-8") as f:
            f.write(snippet)
        console.print(f"[green]✓ Aliases successfully added to {rc_path}[/green]")
    else:
        console.print("[yellow]Cancelled.[/yellow]")


@app.command()
def explain(
    id_: str = typer.Argument(..., metavar="ID", help="Command ID to explain."),
):
    """Use AI to explain what a command does."""
    entry = storage.get_command_by_id(id_)
    if not entry:
        console.print(f"[red]No command found with ID: {id_}[/red]")
        raise typer.Exit(1)

    with console.status("[cyan]Asking AI...[/cyan]"):
        explanation = ai.explain_command(entry["cmd"])

    console.print(Panel(
        f"[bold]Command:[/bold] {entry['cmd']}\n\n{explanation}",
        title="AI Explanation",
        border_style="cyan",
    ))


@app.command()
def delete(
    id_: str = typer.Argument(..., metavar="ID", help="Command ID to delete."),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation."),
):
    """Delete a saved command."""
    entry = storage.get_command_by_id(id_)
    if not entry:
        console.print(f"[red]No command found with ID: {id_}[/red]")
        raise typer.Exit(1)

    console.print(f"[dim]{entry['cmd']}[/dim]")
    if not force and not Confirm.ask(f"Delete [yellow]{id_}[/yellow]?", default=False):
        console.print("[yellow]Cancelled.[/yellow]")
        raise typer.Exit()

    storage.delete_command(id_)
    console.print(f"[green]✓ Deleted {id_}[/green]")


@app.command()
def stats():
    """Show usage statistics for your command clipboard."""
    data = storage.get_stats()

    if data["total"] == 0:
        console.print("[yellow]No commands saved yet.[/yellow]")
        return

    console.print(Panel(
        f"[bold]Total commands:[/bold] {data['total']}\n"
        f"[bold]All tags:[/bold] {', '.join(data['all_tags']) or 'none'}",
        title="cmdclip stats",
        border_style="cyan",
    ))

    if data["top"]:
        table = Table(title="Top 5 Most Used", header_style="bold magenta")
        table.add_column("ID", style="dim", width=10)
        table.add_column("Command", width=45)
        table.add_column("Uses", justify="right", width=6)
        table.add_column("Last used", width=22)

        for c in data["top"]:
            last = c.get("last_used") or "never"
            if last != "never":
                last = last[:19].replace("T", " ")
            table.add_row(c["id"], c["cmd"][:60], str(c.get("use_count", 0)), last)

        console.print(table)


@app.command()
def history():
    """
    Import frequently used commands from shell history.
    Shows commands used 5+ times and lets you pick which to save.
    """
    with console.status("[cyan]Scanning shell history...[/cyan]"):
        candidates = storage.smart_history_import()

    if not candidates:
        console.print("[yellow]No frequently-used commands found (or history file not accessible).[/yellow]")
        return

    console.print(f"[bold cyan]Found {len(candidates)} frequently-used commands:[/bold cyan]\n")

    to_save = []
    for i, cmd in enumerate(candidates, 1):
        console.print(f"[dim]{i:2}.[/dim] {cmd}")
        if Confirm.ask("   Save this?", default=False):
            to_save.append(cmd)

    if not to_save:
        console.print("[yellow]Nothing saved.[/yellow]")
        return

    saved = 0
    for cmd in to_save:
        with console.status(f"[cyan]Tagging: {cmd[:40]}...[/cyan]"):
            tags = ai.suggest_tags(cmd)
        storage.add_command(cmd, tags)
        saved += 1

    console.print(f"[green]✓ Saved {saved} command(s) from history.[/green]")


@app.command()
def export(
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output file path."),
    safe: bool = typer.Option(False, "--safe", help="Exclude dangerous commands."),
):
    """Export all commands to JSON."""
    import json

    data = storage._load_db()
    if safe:
        data["commands"] = [
            c for c in data["commands"]
            if not _is_dangerous(c.get("cmd", ""))
        ]

    json_str = json.dumps(data, indent=2, ensure_ascii=False)

    if output:
        from pathlib import Path
        Path(output).write_text(json_str, encoding="utf-8")
        console.print(f"[green]✓ Exported {len(data['commands'])} commands to {output}[/green]")
    else:
        print(json_str)


@app.command("import")
def import_commands(
    file: str = typer.Argument(..., help="JSON file to import."),
    safe_mode: bool = typer.Option(True, "--safe/--no-safe", help="Quarantine dangerous commands."),
):
    """Import commands from a JSON export file."""
    from pathlib import Path

    path = Path(file)
    if not path.exists():
        console.print(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    json_str = path.read_text(encoding="utf-8")
    try:
        imported, quarantined = storage.import_db(json_str, safe_mode=safe_mode)
    except ValueError as e:
        console.print(f"[red]Import failed: {e}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]✓ Imported {imported} command(s).[/green]")
    if quarantined:
        console.print(
            f"[yellow]⚠ {quarantined} command(s) quarantined (dangerous patterns).[/yellow]\n"
            f"[dim]Review them at: {storage.get_quarantine_path()}[/dim]"
        )


@app.command()
def share(
    id_: str = typer.Argument(..., metavar="ID", help="Command ID to share."),
):
    """Copy a command as a shareable text snippet."""
    entry = storage.get_command_by_id(id_)
    if not entry:
        console.print(f"[red]No command found with ID: {id_}[/red]")
        raise typer.Exit(1)

    tags_str = " ".join(f"#{t}" for t in entry.get("tags", []))
    note = entry.get("note", "")

    snippet = f"```\n{entry['cmd']}\n```"
    if note:
        snippet = f"{note}\n{snippet}"
    if tags_str:
        snippet += f"\n{tags_str}"
    snippet += "\n\n— shared via cmdclip (M5 Dev)"

    if _copy_to_clipboard(snippet):
        console.print("[green]✓ Snippet copied to clipboard — paste it anywhere![/green]")
    else:
        console.print("[bold]Share snippet:[/bold]")
        console.print(snippet)


# ─── Config subcommands ────────────────────────────────────────────────────────

@config_app.command("set-key")
def config_set_key(
    key: str = typer.Argument(..., help="Your Groq API key."),
):
    """Save your Groq API key to local config."""
    storage.set_config("groq_api_key", key)
    console.print("[green]✓ Groq API key saved.[/green]")


@config_app.command("show")
def config_show():
    """Show current configuration (keys are masked)."""
    cfg = storage.get_config()
    if not cfg:
        console.print("[yellow]No configuration set.[/yellow]")
        return
    for k, v in cfg.items():
        if "key" in k.lower():
            v = v[:8] + "..." if len(v) > 8 else "***"
        console.print(f"[cyan]{k}[/cyan]: {v}")


@config_app.command("path")
def config_path():
    """Show the path to cmdclip data directory."""
    console.print(f"[cyan]Data directory:[/cyan] {storage.get_data_dir()}")
    console.print(f"[cyan]Database:[/cyan]      {storage.get_db_path()}")
    console.print(f"[cyan]Config:[/cyan]        {storage.get_config_path()}")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    app()


if __name__ == "__main__":
    main()