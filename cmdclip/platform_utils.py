"""
platform_utils.py — platform and clipboard detection utilities for cmdclip
"""

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from rich.console import Console

console = Console()


def is_termux() -> bool:
    """Returns True if running inside Termux on Android."""
    if "TERMUX_VERSION" in os.environ:
        return True
    if Path("/data/data/com.termux").exists():
        return True
    if shutil.which("termux-clipboard-set") is not None:
        return True
    return False


def is_headless() -> bool:
    """Returns True if no display server is available (no DISPLAY or WAYLAND_DISPLAY)."""
    if platform.system() in ("Windows", "Darwin"):
        return False
    return not bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def get_platform_name() -> str:
    """Returns a friendly string: 'windows', 'macos', 'linux', 'termux'"""
    if is_termux():
        return "termux"
    sys_name = platform.system().lower()
    if sys_name == "windows":
        return "windows"
    elif sys_name == "darwin":
        return "macos"
    else:
        return "linux"


def get_clipboard_backend() -> str:
    """Returns which clipboard backend will be used: 'pyperclip', 'termux-api', 'termux', 'xclip', 'xsel', 'none'"""
    if is_termux() and shutil.which("termux-clipboard-set") is not None:
        return "termux-api"

    try:
        import pyperclip
        backend_func = pyperclip.determine_clipboard()
        if backend_func and backend_func[0] != pyperclip.init_no_clipboard:
            return "pyperclip"
    except Exception:
        pass

    if shutil.which("xclip") is not None:
        return "xclip"

    if shutil.which("xsel") is not None:
        return "xsel"

    if is_termux():
        return "termux"

    return "none"


def copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard using available backend. Returns True on success."""
    # 1. Termux
    if is_termux() and shutil.which("termux-clipboard-set") is not None:
        try:
            res = subprocess.run(["termux-clipboard-set"], input=text.encode("utf-8"), check=False)
            if res.returncode == 0:
                return True
        except Exception:
            pass

    # 2. Pyperclip
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except pyperclip.PyperclipException:
        pass
    except Exception:
        pass

    # 3. xclip
    if shutil.which("xclip") is not None:
        try:
            res = subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode("utf-8"), check=False)
            if res.returncode == 0:
                return True
        except Exception:
            pass

    # 4. xsel
    if shutil.which("xsel") is not None:
        try:
            res = subprocess.run(["xsel", "--clipboard", "--input"], input=text.encode("utf-8"), check=False)
            if res.returncode == 0:
                return True
        except Exception:
            pass

    # 5. Fallback
    if is_termux():
        console.print("[red]Clipboard unavailable.[/red] [yellow]Please install Termux API:[/yellow] pkg install termux-api [yellow]and install Termux:API app.[/yellow]")
    else:
        console.print("[red]Clipboard unavailable.[/red] [yellow]Please install xclip or xsel:[/yellow] sudo apt install xclip")
    return False


def paste_from_clipboard() -> Optional[str]:
    """Paste text from system clipboard using available backend. Returns string or None."""
    # 1. Termux
    if is_termux() and shutil.which("termux-clipboard-get") is not None:
        try:
            res = subprocess.run(["termux-clipboard-get"], capture_output=True, text=True, check=False)
            if res.returncode == 0:
                return res.stdout
        except Exception:
            pass

    # 2. Pyperclip
    try:
        import pyperclip
        return pyperclip.paste()
    except pyperclip.PyperclipException:
        pass
    except Exception:
        pass

    # 3. xclip
    if shutil.which("xclip") is not None:
        try:
            res = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True, check=False)
            if res.returncode == 0:
                return res.stdout
        except Exception:
            pass

    # 4. xsel
    if shutil.which("xsel") is not None:
        try:
            res = subprocess.run(["xsel", "--clipboard", "--output"], capture_output=True, text=True, check=False)
            if res.returncode == 0:
                return res.stdout
        except Exception:
            pass

    # 5. Fallback
    if is_termux():
        console.print("[red]Clipboard unavailable.[/red] [yellow]Please install Termux API:[/yellow] pkg install termux-api [yellow]and install Termux:API app.[/yellow]")
    else:
        console.print("[red]Clipboard unavailable.[/red] [yellow]Please install xclip or xsel:[/yellow] sudo apt install xclip")
    return None
