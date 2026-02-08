"""Parse handoff markdown files into structured metadata."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Extract YAML frontmatter and markdown body.

    Returns a tuple of ``(frontmatter, body)``.
    """
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    try:
        fm = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        logger.warning("Failed to parse YAML frontmatter")
        return {}, content

    return _validate_frontmatter(fm), parts[2].strip()


def parse_handoff(filepath: Path) -> dict[str, Any]:
    """Parse a handoff markdown file into a structured dictionary."""
    content = filepath.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(content)

    date = fm.get("date", "")
    if hasattr(date, "isoformat"):
        date = date.isoformat()

    headline_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    headline = headline_match.group(1).strip() if headline_match else str(fm.get("headline", ""))

    def extract_section(name: str) -> str:
        pattern = rf"^##\s+{re.escape(name)}\s*\n(.*?)(?=^##\s|\Z)"
        match = re.search(pattern, body, re.MULTILINE | re.DOTALL)
        return match.group(1).strip() if match else ""

    duration_str = str(fm.get("duration", ""))

    return {
        "session_id": str(fm.get("session_id", "")),
        "date": str(date),
        "duration": duration_str,
        "duration_minutes": _parse_duration_minutes(duration_str),
        "model": str(fm.get("model", "")),
        "headline": headline,
        "projects": fm.get("projects", []),
        "keywords": fm.get("keywords", []),
        "substance": _safe_int(fm.get("substance", 1), default=1),
        "what_happened": extract_section("What Happened"),
        "insights": extract_section("Insights"),
        "open_threads": extract_section("Open Threads"),
        "raw_body": body,
    }


def _validate_frontmatter(fm: Any) -> dict[str, Any]:
    if not isinstance(fm, dict):
        return {}

    normalized: dict[str, Any] = dict(fm)

    if "session_id" in normalized:
        normalized["session_id"] = str(normalized["session_id"])
    if "date" in normalized:
        normalized["date"] = str(normalized["date"])

    for key in ("projects", "keywords"):
        value = normalized.get(key)
        if value is None:
            normalized[key] = []
        elif isinstance(value, str):
            normalized[key] = [value]
        elif not isinstance(value, list):
            normalized[key] = []

    if "substance" in normalized:
        normalized["substance"] = _safe_int(normalized["substance"], default=1)

    return normalized


def _safe_int(value: Any, default: int = 1) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _parse_duration_minutes(duration_str: str) -> int:
    if not duration_str:
        return 0
    total = 0
    hours = re.search(r"(\d+)\s*h", duration_str)
    minutes = re.search(r"(\d+)\s*m", duration_str)
    if hours:
        total += int(hours.group(1)) * 60
    if minutes:
        total += int(minutes.group(1))
    return total
