"""Detect the active Claude Code session JSONL file."""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

from .config import SESSIONS_DIR, TASKS_DIR

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
STALENESS_WINDOW = 30


def _project_dirs() -> list[Path]:
    """List candidate Claude project directories from the configured sessions path."""
    if not SESSIONS_DIR.is_dir():
        return []

    directories: list[Path] = []
    for entry in SESSIONS_DIR.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            directories.append(entry)
    return directories


def _find_jsonls(directory: Path) -> list[Path]:
    """List JSONL files in one project directory, de-duplicated by inode."""
    seen_inodes: set[int] = set()
    results: list[Path] = []

    for item in directory.glob("*.jsonl"):
        if not item.is_file():
            continue
        inode = item.stat().st_ino
        if inode in seen_inodes:
            continue
        seen_inodes.add(inode)
        results.append(item)

    return results


def _freshest_jsonl(jsonls: list[Path], max_age: float = STALENESS_WINDOW) -> tuple[Path | None, str | None]:
    """Pick the freshest JSONL file modified within ``max_age`` seconds."""
    if not jsonls:
        return None, "no JSONL files found"

    now = time.time()
    candidates: list[tuple[Path, float]] = []

    for path in jsonls:
        try:
            modified = path.stat().st_mtime
        except OSError:
            continue
        if now - modified <= max_age:
            candidates.append((path, modified))

    if not candidates:
        return None, f"no JSONL modified within {max_age}s"

    candidates.sort(key=lambda item: item[1], reverse=True)
    if len(candidates) == 1:
        return candidates[0][0], None

    if candidates[0][1] - candidates[1][1] > 2.0:
        return candidates[0][0], None

    return None, f"ambiguous: {len(candidates)} JSONLs modified within {max_age}s"


def _by_explicit_id(session_id: str) -> tuple[Path | None, str | None]:
    """Find a session JSONL by explicit UUID."""
    if not UUID_RE.fullmatch(session_id):
        return None, f"invalid session_id format: {session_id}"

    for project_dir in _project_dirs():
        candidate = project_dir / f"{session_id}.jsonl"
        if candidate.is_file():
            return candidate, None

    return None, f"session {session_id} not found in {SESSIONS_DIR}"


def _by_pid_tracing() -> tuple[Path | None, str | None]:
    """Use parent-process open file descriptors to infer active session UUID."""
    parent_pid = os.getppid()
    if parent_pid <= 1:
        return None, "PPID is init/launchd, cannot trace"

    try:
        result = subprocess.run(
            ["lsof", "-Fn", "-p", str(parent_pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None, "lsof failed or is unavailable"

    if result.returncode != 0:
        return None, f"lsof returned {result.returncode}"

    tasks_dir_string = str(TASKS_DIR)
    for line in result.stdout.splitlines():
        if not line.startswith("n"):
            continue

        path_str = line[1:]
        if tasks_dir_string not in path_str:
            continue

        match = UUID_RE.search(path_str)
        if not match:
            continue

        session_id = match.group(0)
        found, _ = _by_explicit_id(session_id)
        if found:
            return found, None
        return None, f"session UUID {session_id} found via PID but no JSONL exists"

    return None, "no tasks file descriptor found in parent process"


def _cwd_project_dir() -> Path | None:
    """Map current working directory to Claude's escaped project directory naming."""
    encoded = str(Path.cwd()).replace("/", "-")
    candidate = SESSIONS_DIR / encoded
    if candidate.is_dir() and not candidate.is_symlink():
        return candidate
    return None


def _by_cwd_mtime() -> tuple[Path | None, str | None]:
    """Find freshest JSONL in the project directory derived from current CWD."""
    project_dir = _cwd_project_dir()
    if not project_dir:
        return None, "could not derive project dir from current working directory"

    return _freshest_jsonl(_find_jsonls(project_dir))


def _by_global_mtime() -> tuple[Path | None, str | None]:
    """Find freshest JSONL across all project directories."""
    all_jsonls: list[Path] = []
    for project_dir in _project_dirs():
        all_jsonls.extend(_find_jsonls(project_dir))
    return _freshest_jsonl(all_jsonls)


def detect_session(explicit_session_id: str | None = None) -> tuple[Path | None, str | None]:
    """Detect the active session JSONL path.

    Detection order:
    1. Explicit session ID
    2. PID tracing
    3. CWD-scoped mtime
    4. Global mtime fallback
    """
    if explicit_session_id:
        path, error = _by_explicit_id(explicit_session_id)
        if path:
            return path, None
        return None, f"explicit_id: {error}" if error else "explicit_id: unknown error"

    strategies: list[tuple[str, Callable[[], tuple[Path | None, str | None]]]] = [
        ("pid_tracing", _by_pid_tracing),
        ("cwd_mtime", _by_cwd_mtime),
        ("global_mtime", _by_global_mtime),
    ]

    errors: list[str] = []
    for name, strategy in strategies:
        path, error = strategy()
        if path:
            return path, None
        if error:
            errors.append(f"{name}: {error}")

    return None, "; ".join(errors) if errors else "no detection strategies available"
