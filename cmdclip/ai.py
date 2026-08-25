"""
ai.py — Flexible AI Backend System for cmdclip
Provides pluggable AI providers (groq, openrouter, ollama, lmstudio, openai, custom, none)
and public API: explain_command(), suggest_tags().
"""

import json
import os
import urllib.error
import urllib.request
from typing import Optional

VALID_BACKENDS = ["groq", "openrouter", "ollama", "lmstudio", "openai", "custom", "none"]

SYSTEM_PROMPT_EXPLAIN = (
    "You are a shell command explainer. "
    "Given a shell command, explain what it does clearly and concisely "
    "in 2-4 sentences. Mention any risks if relevant. "
    "Reply in plain text, no markdown."
)

SYSTEM_PROMPT_TAGS = (
    "You are a shell command tagger. "
    "Given a shell command, respond ONLY with a JSON array of 2-4 lowercase tag strings. "
    "Tags should be tool names or categories like: docker, git, network, files, ops, python, ssh, etc. "
    "Example output: [\"docker\", \"containers\", \"ops\"] "
    "No explanation, no markdown, just the JSON array."
)


def _is_server_reachable(url: str, timeout: float = 2.0) -> bool:
    """Check if a HTTP server is reachable within timeout seconds."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        # Received HTTP response (e.g., 404, 401, 405), server is reachable
        return True
    except Exception:
        return False


def get_backend() -> str:
    """
    Returns which backend to use.
    Priority:
    1. Config file key 'ai_backend' if set
    2. Auto-detect: check env vars and local servers in order
    3. Fallback: 'none'
    """
    from cmdclip.storage import get_config

    cfg = get_config()
    configured = cfg.get("ai_backend")
    if configured and configured in VALID_BACKENDS:
        return configured

    # Auto-detection sequence
    if os.environ.get("GROQ_API_KEY"):
        return "groq"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if _is_server_reachable("http://localhost:11434", timeout=2.0):
        return "ollama"
    if _is_server_reachable("http://localhost:1234", timeout=2.0):
        return "lmstudio"
    if cfg.get("custom_ai_url"):
        return "custom"
    if cfg.get("groq_api_key"):
        return "groq"
    if cfg.get("openrouter_api_key"):
        return "openrouter"

    return "none"


def _get_model_for_backend(backend: str, override_model: Optional[str] = None) -> str:
    if override_model:
        return override_model

    from cmdclip.storage import get_config

    cfg = get_config()

    if backend == "ollama":
        return cfg.get("ollama_model") or cfg.get("ai_model") or "llama3"
    elif backend == "lmstudio":
        return cfg.get("lmstudio_model") or cfg.get("ai_model") or "local-model"
    elif backend == "custom":
        return cfg.get("custom_ai_model") or cfg.get("ai_model") or ""
    elif backend == "groq":
        return cfg.get("ai_model") or "llama3-8b-8192"
    elif backend == "openrouter":
        return cfg.get("ai_model") or "mistralai/mistral-7b-instruct:free"
    elif backend == "openai":
        return cfg.get("ai_model") or "gpt-4o-mini"

    return cfg.get("ai_model") or ""


def _get_key_for_backend(backend: str) -> Optional[str]:
    from cmdclip.storage import get_config

    cfg = get_config()

    if backend == "groq":
        return os.environ.get("GROQ_API_KEY") or cfg.get("groq_api_key")
    elif backend == "openrouter":
        return os.environ.get("OPENROUTER_API_KEY") or cfg.get("openrouter_api_key")
    elif backend == "openai":
        return os.environ.get("OPENAI_API_KEY") or cfg.get("openai_api_key")
    elif backend == "custom":
        return cfg.get("custom_ai_key") or ""
    elif backend in ("ollama", "lmstudio"):
        return ""
    return None


def _get_endpoint_for_backend(backend: str) -> str:
    from cmdclip.storage import get_config

    cfg = get_config()

    if backend == "groq":
        return "https://api.groq.com/openai/v1"
    elif backend == "openrouter":
        return "https://openrouter.ai/api/v1"
    elif backend == "ollama":
        return "http://localhost:11434/v1"
    elif backend == "lmstudio":
        return "http://localhost:1234/v1"
    elif backend == "openai":
        return "https://api.openai.com/v1"
    elif backend == "custom":
        return cfg.get("custom_ai_url", "")
    return ""


def _call_openai_compatible(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 200,
    extra_headers: Optional[dict] = None,
) -> str:
    """
    Generic function to call any OpenAI-compatible API.
    Used by: openrouter, ollama, lmstudio, openai, custom, groq.
    Returns the response text or raises an exception.
    """
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra_headers:
        headers.update(extra_headers)

    url = f"{base_url.rstrip('/')}/chat/completions"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API error {e.code}: {e.read().decode()}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connection failed: {e.reason}")


def _call_backend(
    backend: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 200,
    override_model: Optional[str] = None,
) -> str:
    if backend == "none":
        raise RuntimeError("AI is disabled ('none' backend active)")

    endpoint = _get_endpoint_for_backend(backend)
    if not endpoint:
        raise RuntimeError(f"No endpoint configured for {backend} backend")

    if backend in ("ollama", "lmstudio"):
        check_url = "http://localhost:11434" if backend == "ollama" else "http://localhost:1234"
        if not _is_server_reachable(check_url, timeout=2.0):
            raise RuntimeError(f"{backend} server is not reachable on {check_url}")

    key = _get_key_for_backend(backend)
    if backend in ("groq", "openrouter", "openai") and not key:
        raise RuntimeError(f"No API key found for {backend} backend")

    model = _get_model_for_backend(backend, override_model=override_model)
    if not model:
        raise RuntimeError(f"No model specified for {backend} backend")

    extra_headers = None
    if backend == "openrouter":
        extra_headers = {
            "HTTP-Referer": "https://github.com/M5Develop/cmdclip",
            "X-Title": "cmdclip",
        }

    return _call_openai_compatible(
        base_url=endpoint,
        api_key=key or "",
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        extra_headers=extra_headers,
    )


def explain_command(cmd: str, model: Optional[str] = None) -> str:
    """Return a plain-English explanation of a shell command."""
    backend = get_backend()
    if backend == "none":
        return "[!] AI is disabled. Use 'cmdclip config set-backend <backend>' to enable."

    try:
        return _call_backend(
            backend=backend,
            system_prompt=SYSTEM_PROMPT_EXPLAIN,
            user_prompt=f"Explain this command:\n\n{cmd}",
            max_tokens=200,
            override_model=model,
        )
    except Exception as e:
        return f"[!] AI error: {e}"


def suggest_tags(cmd: str, model: Optional[str] = None) -> list[str]:
    """
    Use AI to suggest relevant tags for a command.
    Returns a list of lowercase tag strings.
    Falls back to simple keyword matching if AI is unavailable or fails.
    """
    backend = get_backend()
    if backend == "none":
        return _fallback_tags(cmd)

    try:
        raw = _call_backend(
            backend=backend,
            system_prompt=SYSTEM_PROMPT_TAGS,
            user_prompt=f"Suggest tags for:\n\n{cmd}",
            max_tokens=60,
            override_model=model,
        )
        tags = json.loads(raw)
        if isinstance(tags, list):
            return [str(t).lower().strip() for t in tags if t][:4]
    except Exception:
        pass

    return _fallback_tags(cmd)


def _fallback_tags(cmd: str) -> list[str]:
    """Simple keyword-based tag fallback when AI is unavailable."""
    cmd_lower = cmd.lower()
    keyword_map = {
        "docker": ["docker", "containers"],
        "git": ["git", "vcs"],
        "ssh": ["ssh", "remote"],
        "python": ["python"],
        "pip": ["python", "pip"],
        "apt": ["apt", "linux", "packages"],
        "brew": ["brew", "macos", "packages"],
        "npm": ["npm", "node", "js"],
        "curl": ["curl", "network", "http"],
        "wget": ["wget", "network", "download"],
        "ffmpeg": ["ffmpeg", "media"],
        "grep": ["grep", "search", "text"],
        "find": ["find", "files"],
        "rm": ["files", "cleanup"],
        "chmod": ["files", "permissions"],
        "systemctl": ["systemd", "services", "linux"],
        "kubectl": ["kubernetes", "k8s", "ops"],
        "tar": ["archive", "files"],
        "rsync": ["rsync", "files", "sync"],
    }
    tags = []
    for keyword, t in keyword_map.items():
        if keyword in cmd_lower:
            tags.extend(t)
    return list(dict.fromkeys(tags))[:4] or ["general"]
