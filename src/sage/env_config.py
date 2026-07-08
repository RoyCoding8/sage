"""
Environment Configuration — Centralized .env + env var loading.

Priority order (first match wins):
1. Real environment variables (e.g., SAGE_QWEN_API_KEY set in shell)
2. .env file in project directory
3. .env file in home directory (~/.sage/.env)
4. File-based Qwen secret (~/.openclaw/secrets/)

This module is imported once at startup; no repeated disk reads.
"""

import os
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# All recognized Sage environment variables
# Note: Alibaba Cloud credentials are entered via the web UI and stored in
# server memory only — not as environment variables.
ENV_KEYS = {
    "SAGE_QWEN_API_KEY": "Qwen Cloud API key",
}

# File-based secret fallbacks (relative to home dir)
SECRET_FILE_MAP = {
    "SAGE_QWEN_API_KEY": Path.home()
    / ".openclaw"
    / "secrets"
    / "qwen-cloud-api-key.txt",
}


def load_dotenv(project_dir: Optional[str] = None, home_dir: Optional[str] = None):
    """Load .env files into os.environ (without overriding existing vars).

    Searches in order:
    1. {project_dir}/.env
    2. Repository root .env (where this package is installed from)
    3. ~/.sage/.env

    Only sets variables that are NOT already in the environment.
    """
    search_paths = []

    if project_dir:
        search_paths.append(Path(project_dir) / ".env")

    # Always check the repo root (parent of src/sage/) — handles the case where
    # project_dir is a sandbox like .local/demo but .env lives at the repo root.
    repo_root = Path(__file__).resolve().parent.parent.parent
    repo_env = repo_root / ".env"
    if repo_env not in search_paths:
        search_paths.append(repo_env)

    sage_home = Path(home_dir) if home_dir else Path.home() / ".sage"
    search_paths.append(sage_home / ".env")

    for env_path in search_paths:
        if env_path.exists():
            _load_single_dotenv(env_path)


def _load_single_dotenv(path: Path):
    """Parse a single .env file and set missing env vars.

    Format: KEY=VALUE (one per line)
    - Lines starting with # are comments
    - Empty lines are ignored
    - Values can be optionally quoted with single or double quotes
    - Whitespace around = is stripped
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to read %s: %s", path, e)
        return

    loaded = 0
    for line_num, line in enumerate(content.splitlines(), 1):
        line = line.strip()

        # Skip comments and empty lines
        if not line or line.startswith("#"):
            continue

        # Parse KEY=VALUE
        if "=" not in line:
            logger.warning("Skipping malformed line %d in %s: %s", line_num, path, line)
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # Remove surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]

        # Only set if not already in environment (real env vars win)
        if key not in os.environ:
            os.environ[key] = value
            loaded += 1

    logger.debug("Loaded %d variables from %s", loaded, path)


def get_env_summary(project_dir: Optional[str] = None) -> dict:
    """Return a summary of all Sage env vars and their status.

    Returns dict with each var name mapped to:
    - "set": bool (whether the var is available)
    - "source": where it came from ("env", "dotenv", "file", or "missing")
    - "description": human-readable description
    """
    # Load .env first to ensure we have the latest
    load_dotenv(project_dir)

    summary = {}
    for var_name, description in ENV_KEYS.items():
        value = os.environ.get(var_name, "")
        if value:
            source = "env"
        elif (fp := SECRET_FILE_MAP.get(var_name)) and fp.exists():
            source, value = "file", "<loaded from file>"
        else:
            source, value = "missing", ""

        if value and key_is_sensitive(var_name):
            display = f"{value[:4]}...{value[-4:]}" if len(value) > 8 else "****"
        elif value:
            display = value
        else:
            display = "(not set)"

        summary[var_name] = {
            "set": bool(value),
            "source": source,
            "description": description,
            "display": display,
        }

    return summary


def key_is_sensitive(var_name: str) -> bool:
    """Return True if the variable contains a secret/key."""
    sensitive_keywords = ["KEY", "SECRET", "TOKEN", "PASSWORD"]
    return any(kw in var_name.upper() for kw in sensitive_keywords)


if __name__ == "__main__":
    # Quick test
    load_dotenv()
    summary = get_env_summary()
    print("Sage Environment Configuration")
    print("=" * 50)
    for var_name, info in summary.items():
        status = "✅" if info["set"] else "❌"
        print(f"  {status} {var_name}: {info['display']} ({info['source']})")
