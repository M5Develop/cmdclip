"""
Tests for cmdclip cross-platform features, AI backends, and CLI functionality.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from cmdclip import platform_utils, storage, cli, ai

runner = CliRunner()


@pytest.fixture
def tmp_storage_dir(monkeypatch, tmp_path):
    d = tmp_path / "cmdclip_data"
    d.mkdir()
    monkeypatch.setattr(storage, "get_data_dir", lambda: d)
    return d


# ─── platform_utils Tests ─────────────────────────────────────────────────────

def test_is_termux(monkeypatch, tmp_path):
    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    monkeypatch.setattr(shutil, "which", lambda cmd: None)

    with patch("cmdclip.platform_utils.Path.exists", return_value=False):
        assert not platform_utils.is_termux()

    monkeypatch.setenv("TERMUX_VERSION", "1.0")
    assert platform_utils.is_termux()

    monkeypatch.delenv("TERMUX_VERSION")
    with patch("cmdclip.platform_utils.Path.exists", lambda self: str(self) == "/data/data/com.termux"):
        assert platform_utils.is_termux()

    with patch("cmdclip.platform_utils.Path.exists", return_value=False):
        monkeypatch.setattr(shutil, "which", lambda cmd: "/bin/termux-clipboard-set" if cmd == "termux-clipboard-set" else None)
        assert platform_utils.is_termux()


def test_is_headless(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    with patch("platform.system", return_value="Linux"):
        assert platform_utils.is_headless()

    monkeypatch.setenv("DISPLAY", ":0")
    with patch("platform.system", return_value="Linux"):
        assert not platform_utils.is_headless()

    monkeypatch.delenv("DISPLAY")
    with patch("platform.system", return_value="Windows"):
        assert not platform_utils.is_headless()


def test_get_platform_name(monkeypatch):
    with patch("cmdclip.platform_utils.is_termux", return_value=True):
        assert platform_utils.get_platform_name() == "termux"

    with patch("cmdclip.platform_utils.is_termux", return_value=False):
        with patch("platform.system", return_value="Windows"):
            assert platform_utils.get_platform_name() == "windows"
        with patch("platform.system", return_value="Darwin"):
            assert platform_utils.get_platform_name() == "macos"
        with patch("platform.system", return_value="Linux"):
            assert platform_utils.get_platform_name() == "linux"


def test_get_clipboard_backend(monkeypatch):
    with patch("cmdclip.platform_utils.is_termux", return_value=True):
        monkeypatch.setattr(shutil, "which", lambda c: "/usr/bin/termux-clipboard-set" if c == "termux-clipboard-set" else None)
        assert platform_utils.get_clipboard_backend() == "termux-api"

    with patch("cmdclip.platform_utils.is_termux", return_value=False):
        with patch("pyperclip.determine_clipboard", side_effect=Exception("No pyperclip")):
            monkeypatch.setattr(shutil, "which", lambda c: "/usr/bin/xclip" if c == "xclip" else None)
            assert platform_utils.get_clipboard_backend() == "xclip"


def test_copy_to_clipboard_fallbacks(monkeypatch):
    # Test termux branch
    with patch("cmdclip.platform_utils.is_termux", return_value=True):
        monkeypatch.setattr(shutil, "which", lambda c: "/usr/bin/termux-clipboard-set" if c == "termux-clipboard-set" else None)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(return_code=0, returncode=0)
            assert platform_utils.copy_to_clipboard("echo termux")
            mock_run.assert_called_with(["termux-clipboard-set"], input=b"echo termux", check=False)

    # Test failure fallback message
    with patch("cmdclip.platform_utils.is_termux", return_value=False):
        monkeypatch.setattr(shutil, "which", lambda c: None)
        with patch("pyperclip.copy", side_effect=Exception("No pyperclip")):
            res = platform_utils.copy_to_clipboard("echo test")
            assert res is False


# ─── History Path Tests ────────────────────────────────────────────────────────

def test_get_history_path(monkeypatch, tmp_path):
    hist_file = tmp_path / "custom_hist"
    hist_file.write_text("ls -la")
    monkeypatch.setenv("HISTFILE", str(hist_file))

    assert storage.get_history_path() == hist_file

    monkeypatch.delenv("HISTFILE")
    zsh_hist = tmp_path / ".zsh_history"
    zsh_hist.write_text("echo zsh")
    monkeypatch.setenv("SHELL", "/bin/zsh")

    with patch("pathlib.Path.home", return_value=tmp_path):
        assert storage.get_history_path() == zsh_hist


# ─── CLI Command Tests ─────────────────────────────────────────────────────────

def test_version_flag():
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert "cmdclip 1.0.1" in result.output
    assert "Platform:" in result.output
    assert "Clipboard backend:" in result.output
    assert "Python:" in result.output


def test_run_command_default_copy(tmp_storage_dir):
    entry = storage.add_command("echo hello_test", ["test"])
    cmd_id = entry["id"]

    with patch("cmdclip.cli._copy_to_clipboard", return_value=True) as mock_copy:
        result = runner.invoke(cli.app, ["run", cmd_id])
        assert result.exit_code == 0
        mock_copy.assert_called_once_with("echo hello_test")
        assert "Copied to clipboard" in result.output


def test_run_command_exec(tmp_storage_dir):
    entry = storage.add_command("echo hello_exec", ["test"])
    cmd_id = entry["id"]

    with patch("cmdclip.cli._run_command") as mock_exec:
        result = runner.invoke(cli.app, ["run", cmd_id, "--exec"])
        assert result.exit_code == 0
        mock_exec.assert_called_once_with("echo hello_exec")


def test_termux_setup_on_non_termux():
    with patch("cmdclip.platform_utils.is_termux", return_value=False):
        result = runner.invoke(cli.app, ["termux-setup"])
        assert result.exit_code == 1
        assert "Error: termux-setup is only supported on Android Termux." in result.output


def test_termux_setup_on_termux(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/bash")
    rc_file = tmp_path / ".bashrc"
    with patch("cmdclip.platform_utils.is_termux", return_value=True):
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = runner.invoke(cli.app, ["termux-setup"], input="y\n")
            assert result.exit_code == 0
            assert rc_file.exists()
            content = rc_file.read_text()
            assert "alias cc='cmdclip run --exec'" in content


# ─── AI Backend System Tests ───────────────────────────────────────────────────

def test_get_backend_autodetect_order(tmp_storage_dir, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with patch("cmdclip.ai._is_server_reachable", return_value=False):
        assert ai.get_backend() == "none"

    # 1. GROQ_API_KEY
    monkeypatch.setenv("GROQ_API_KEY", "gsk_123")
    assert ai.get_backend() == "groq"

    # 2. OPENROUTER_API_KEY
    monkeypatch.delenv("GROQ_API_KEY")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-123")
    assert ai.get_backend() == "openrouter"

    # 3. OPENAI_API_KEY
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-123")
    assert ai.get_backend() == "openai"

    # 4. Ollama reachable
    monkeypatch.delenv("OPENAI_API_KEY")
    def mock_reachable(url, timeout=2.0):
        return "11434" in url
    with patch("cmdclip.ai._is_server_reachable", side_effect=mock_reachable):
        assert ai.get_backend() == "ollama"

    # 5. LM Studio reachable
    def mock_lm_reachable(url, timeout=2.0):
        return "1234" in url
    with patch("cmdclip.ai._is_server_reachable", side_effect=mock_lm_reachable):
        assert ai.get_backend() == "lmstudio"

    # 6. custom_ai_url in config
    with patch("cmdclip.ai._is_server_reachable", return_value=False):
        storage.set_config("custom_ai_url", "http://my-ai/v1")
        assert ai.get_backend() == "custom"

    # 7. groq_api_key in config
    storage.set_config("custom_ai_url", "")
    storage.set_config("groq_api_key", "gsk_saved")
    assert ai.get_backend() == "groq"

    # 8. openrouter_api_key saved
    storage.set_config("groq_api_key", "")
    storage.set_config("openrouter_api_key", "sk-or-saved")
    assert ai.get_backend() == "openrouter"

    # Config set overrides auto-detect
    storage.set_config("ai_backend", "ollama")
    assert ai.get_backend() == "ollama"


def test_explain_command_none_backend(tmp_storage_dir):
    storage.set_config("ai_backend", "none")
    res = ai.explain_command("ls -la")
    assert "[!] AI is disabled" in res


def test_call_openai_compatible(monkeypatch):
    class MockResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return json.dumps({"choices": [{"message": {"content": "Test explanation"}}]}).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=MockResponse()):
        res = ai._call_openai_compatible(
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-4o-mini",
            system_prompt="sys",
            user_prompt="user",
        )
        assert res == "Test explanation"


def test_cli_config_commands(tmp_storage_dir):
    # set-backend
    res = runner.invoke(cli.app, ["config", "set-backend", "openrouter"])
    assert res.exit_code == 0
    assert storage.get_config().get("ai_backend") == "openrouter"

    # set-model
    res = runner.invoke(cli.app, ["config", "set-model", "mistral-large"])
    assert res.exit_code == 0
    assert storage.get_config().get("ai_model") == "mistral-large"

    # set-key with auto-detect backend
    res = runner.invoke(cli.app, ["config", "set-key", "sk-or-testkey"])
    assert res.exit_code == 0
    assert storage.get_config().get("openrouter_api_key") == "sk-or-testkey"

    # set-custom
    res = runner.invoke(cli.app, ["config", "set-custom", "http://localhost:8080/v1", "my-model", "--key", "secret"])
    assert res.exit_code == 0
    cfg = storage.get_config()
    assert cfg.get("custom_ai_url") == "http://localhost:8080/v1"
    assert cfg.get("custom_ai_model") == "my-model"
    assert cfg.get("custom_ai_key") == "secret"

    # ai-status
    res = runner.invoke(cli.app, ["config", "ai-status"])
    assert res.exit_code == 0
    assert "AI Backend Status" in res.output


def test_model_override_in_commands(tmp_storage_dir):
    storage.set_config("ai_backend", "none")
    entry = storage.add_command("git status", ["git"])

    with patch("cmdclip.ai.explain_command", return_value="Git status explanation") as mock_explain:
        res = runner.invoke(cli.app, ["explain", entry["id"], "--model", "llama3-70b"])
        assert res.exit_code == 0
        mock_explain.assert_called_once_with("git status", model="llama3-70b")
