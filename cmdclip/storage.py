"""
storage.py — persistent JSON storage for cmdclip
Handles all read/write operations for commands and config.
"""

import json
import os
import platform
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from cmdclip import platform_utils


# ─── Paths ────────────────────────────────────────────────────────────────────

def get_data_dir() -> Path:
    """Return the platform-appropriate data directory."""
    if platform_utils.get_platform_name() == "windows":
        base = Path.home() / "AppData" / "Local" / "cmdclip"
    else:
        base = Path.home() / ".cmdclip"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_db_path() -> Path:
    return get_data_dir() / "db.json"


def get_config_path() -> Path:
    return get_data_dir() / "config.json"


def get_quarantine_path() -> Path:
    return get_data_dir() / "quarantine.json"


def get_history_path() -> Optional[Path]:
    """Return the shell history file path for the current OS / environment."""
    histfile = os.environ.get("HISTFILE")
    if histfile:
        p = Path(histfile)
        if p.exists():
            return p

    shell = os.environ.get("SHELL", "")
    if "zsh" in shell:
        p = Path.home() / ".zsh_history"
        if p.exists():
            return p

    p = Path.home() / ".bash_history"
    if p.exists():
        return p

    p = Path("/data/data/com.termux/files/home/.bash_history")
    if p.exists():
        return p

    return None


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _load_db() -> dict:
    path = get_db_path()
    if not path.exists():
        return {"commands": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"commands": []}


def _save_db(data: dict) -> None:
    get_db_path().write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def _load_quarantine() -> dict:
    path = get_quarantine_path()
    if not path.exists():
        return {"commands": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"commands": []}


def _save_quarantine(data: dict) -> None:
    get_quarantine_path().write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


# ─── Config ───────────────────────────────────────────────────────────────────

def get_config() -> dict:
    path = get_config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def set_config(key: str, value: str) -> None:
    cfg = get_config()
    cfg[key] = value
    get_config_path().write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


# ─── CRUD ─────────────────────────────────────────────────────────────────────

def add_command(
    cmd: str,
    tags: list[str],
    note: str = "",
    name: str = "",
    template: bool = False,
) -> dict:
    """Add a new command entry and return it."""
    db = _load_db()
    entry = {
        "id": uuid.uuid4().hex[:8],
        "cmd": cmd,
        "name": name,
        "tags": tags,
        "note": note,
        "template": template,
        "use_count": 0,
        "last_used": None,
        "created_at": datetime.now().isoformat(),
    }
    db["commands"].append(entry)
    _save_db(db)
    return entry


def get_all_commands() -> list[dict]:
    return _load_db().get("commands", [])


def get_command_by_id(id_: str) -> Optional[dict]:
    for cmd in get_all_commands():
        if cmd["id"] == id_:
            return cmd
    return None


def search_commands(query: str, tag: Optional[str] = None) -> list[dict]:
    """Fuzzy search across cmd, name, note, tags."""
    results = get_all_commands()

    if tag:
        results = [c for c in results if tag.lower() in [t.lower() for t in c.get("tags", [])]]

    if query:
        q = query.lower()
        results = [
            c for c in results
            if q in c["cmd"].lower()
            or q in c.get("name", "").lower()
            or q in c.get("note", "").lower()
            or any(q in t.lower() for t in c.get("tags", []))
        ]

    return results


def delete_command(id_: str) -> bool:
    db = _load_db()
    before = len(db["commands"])
    db["commands"] = [c for c in db["commands"] if c["id"] != id_]
    if len(db["commands"]) < before:
        _save_db(db)
        return True
    return False


def increment_use_count(id_: str) -> None:
    db = _load_db()
    for cmd in db["commands"]:
        if cmd["id"] == id_:
            cmd["use_count"] = cmd.get("use_count", 0) + 1
            cmd["last_used"] = datetime.now().isoformat()
            break
    _save_db(db)


def get_stats() -> dict:
    commands = get_all_commands()
    if not commands:
        return {"total": 0, "top": [], "all_tags": []}

    top = sorted(commands, key=lambda c: c.get("use_count", 0), reverse=True)[:5]
    all_tags: list[str] = []
    for c in commands:
        all_tags.extend(c.get("tags", []))
    unique_tags = sorted(set(all_tags))

    return {
        "total": len(commands),
        "top": top,
        "all_tags": unique_tags,
    }


# ─── Import / Export ──────────────────────────────────────────────────────────

def export_db() -> str:
    return json.dumps(_load_db(), indent=2, ensure_ascii=False)


def import_db(json_str: str, safe_mode: bool = False) -> tuple[int, int]:
    """
    Import commands from JSON string.
    Returns (imported_count, quarantined_count).
    """
    DANGEROUS_PATTERNS = ["rm -rf", "sudo rm", "DROP TABLE", "mkfs", ":(){:|:&};:"]

    try:
        incoming = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}")

    commands = incoming.get("commands", [])
    db = _load_db()
    quarantine_db = _load_quarantine()

    existing_ids = {c["id"] for c in db["commands"]}
    imported = 0
    quarantined = 0

    for entry in commands:
        if entry.get("id") in existing_ids:
            continue

        if safe_mode and any(p in entry.get("cmd", "") for p in DANGEROUS_PATTERNS):
            quarantine_db["commands"].append(entry)
            quarantined += 1
        else:
            db["commands"].append(entry)
            imported += 1

    _save_db(db)
    if quarantined:
        _save_quarantine(quarantine_db)

    return imported, quarantined


def smart_history_import() -> list[str]:
    """
    Read shell history and return commands that appear 5+ times,
    excluding already-saved commands.
    """
    path = get_history_path()
    if not path:
        return []

    raw = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    # zsh history has timestamps like `: 1700000000:0;actual command`
    cleaned = []
    for line in raw:
        line = line.strip()
        if line.startswith(": ") and ";" in line:
            line = line.split(";", 1)[-1]
        if line and not line.startswith("#"):
            cleaned.append(line)

    from collections import Counter
    counts = Counter(cleaned)

    existing_cmds = {c["cmd"] for c in get_all_commands()}
    candidates = [
        cmd for cmd, count in counts.most_common(30)
        if count >= 5 and cmd not in existing_cmds and len(cmd) > 4
    ]

    return candidates