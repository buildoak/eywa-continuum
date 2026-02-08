"""Central configuration for Eywa.

Configuration is controlled through environment variables with sensible defaults.
"""

from __future__ import annotations

import os
from pathlib import Path


def _path_from_env(env_var: str, default: str) -> Path:
    """Resolve a filesystem path from environment with user expansion."""
    return Path(os.getenv(env_var, default)).expanduser()


def _float_from_env(env_var: str, default: float) -> float:
    """Resolve a float from environment, falling back to default on parse errors."""
    raw = os.getenv(env_var)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_from_env(env_var: str, default: int) -> int:
    """Resolve an int from environment, falling back to default on parse errors."""
    raw = os.getenv(env_var)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


PACKAGE_DIR = Path(__file__).resolve().parent
EXTRACTORS_DIR = PACKAGE_DIR / "extractors"

DATA_DIR = _path_from_env("EYWA_DATA_DIR", str(Path.home() / ".eywa"))
HANDOFFS_DIR = DATA_DIR / "handoffs"
INDEX_PATH = DATA_DIR / "handoff-index.json"

SESSIONS_DIR = _path_from_env(
    "EYWA_SESSIONS_DIR", str(Path.home() / ".claude" / "projects")
)
TASKS_DIR = _path_from_env("EYWA_TASKS_DIR", str(SESSIONS_DIR.parent / "tasks"))

CLAUDE_MODEL = os.getenv("EYWA_CLAUDE_MODEL", "sonnet")
OPENROUTER_MODEL = os.getenv(
    "EYWA_OPENROUTER_MODEL", "google/gemini-3-flash-preview"
)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
BATCH_DELAY = _float_from_env("EYWA_BATCH_DELAY", 0.5)
BATCH_CONCURRENCY = _int_from_env("EYWA_BATCH_CONCURRENCY", 5)
TIMEZONE = os.getenv("EYWA_TIMEZONE", "UTC")
LOG_LEVEL = os.getenv("EYWA_LOG_LEVEL", "INFO").upper()


def ensure_data_dirs() -> None:
    """Create runtime directories when missing."""
    HANDOFFS_DIR.mkdir(parents=True, exist_ok=True)
