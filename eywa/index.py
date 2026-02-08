"""Maintain the handoff index used by retrieval."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import HANDOFFS_DIR, INDEX_PATH
from .parse import parse_handoff

logger = logging.getLogger(__name__)


def handoff_to_index_entry(parsed: dict[str, Any]) -> dict[str, Any]:
    """Convert parsed handoff metadata to an index entry."""
    return {
        "date": parsed["date"],
        "headline": parsed["headline"],
        "projects": parsed["projects"],
        "keywords": parsed["keywords"],
        "substance": parsed["substance"],
        "duration_minutes": parsed["duration_minutes"],
    }


def update_index(entry: dict[str, Any], session_id: str, index_path: Path | None = None) -> bool:
    """Insert or update one handoff entry in the index with file locking."""
    normalized_session_id = str(session_id)
    if len(normalized_session_id) < 4:
        logger.error("Invalid session_id for index update: %r", session_id)
        return False

    target = index_path or INDEX_PATH
    lock_path = target.with_suffix(".lock")
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        with lock_path.open("a+") as lock_handle:
            fcntl.flock(lock_handle, fcntl.LOCK_EX)
            try:
                if target.exists():
                    try:
                        with target.open("r", encoding="utf-8") as handle:
                            index = json.load(handle)
                    except json.JSONDecodeError:
                        backup_path = target.with_suffix(
                            f".corrupt.{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
                        )
                        shutil.copy2(target, backup_path)
                        logger.warning("Corrupt index backed up to %s", backup_path)
                        index = _empty_index()
                else:
                    index = _empty_index()

                _merge_entry(index, normalized_session_id, entry)
                _update_meta(index)
                _write_json_atomic(target, index)
            finally:
                fcntl.flock(lock_handle, fcntl.LOCK_UN)

        return True
    except OSError:
        logger.exception("Failed to update index at %s", target)
        return False


def rebuild_index(handoffs_dir: Path | None = None, index_path: Path | None = None) -> dict[str, Any]:
    """Rebuild the full index by scanning all markdown handoff files."""
    source_dir = handoffs_dir or HANDOFFS_DIR
    target = index_path or INDEX_PATH

    index = _empty_index()

    for handoff_file in sorted(source_dir.glob("**/*.md")):
        try:
            parsed = parse_handoff(handoff_file)
        except OSError:
            logger.warning("Skipping unreadable handoff file: %s", handoff_file)
            continue

        session_id = str(parsed.get("session_id", ""))
        if not session_id:
            logger.warning("Skipping %s: missing session_id", handoff_file)
            continue

        entry = handoff_to_index_entry(parsed)
        _merge_entry(index, session_id, entry)

    _update_meta(index)
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(target, index)
    return index


def _merge_entry(index: dict[str, Any], session_id: str, entry: dict[str, Any]) -> None:
    existing = index["handoffs"].get(session_id)
    if existing:
        _remove_from_inverted(index["by_project"], session_id, existing.get("projects", []))
        _remove_from_inverted(index["by_keyword"], session_id, existing.get("keywords", []))

    index["handoffs"][session_id] = entry

    for project in entry.get("projects", []):
        _append_unique(index["by_project"], str(project), session_id)

    for keyword in entry.get("keywords", []):
        _append_unique(index["by_keyword"], str(keyword), session_id)


def _append_unique(mapping: dict[str, list[str]], key: str, value: str) -> None:
    values = mapping.setdefault(key, [])
    if value not in values:
        values.append(value)


def _remove_from_inverted(mapping: dict[str, list[str]], session_id: str, keys: list[Any]) -> None:
    for key in keys:
        key_str = str(key)
        values = mapping.get(key_str)
        if not values:
            continue
        mapping[key_str] = [value for value in values if value != session_id]
        if not mapping[key_str]:
            mapping.pop(key_str, None)


def _update_meta(index: dict[str, Any]) -> None:
    dates = [h.get("date") for h in index["handoffs"].values() if h.get("date")]
    meta = index["meta"]
    meta["last_updated"] = datetime.now(timezone.utc).isoformat()
    meta["handoff_count"] = len(index["handoffs"])
    meta["date_range"] = [min(dates), max(dates)] if dates else []


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    fd, temp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except OSError:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _empty_index() -> dict[str, Any]:
    return {
        "meta": {
            "last_updated": None,
            "handoff_count": 0,
            "date_range": [],
        },
        "handoffs": {},
        "by_project": {},
        "by_keyword": {},
    }
