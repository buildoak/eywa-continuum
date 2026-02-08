"""Extract and persist handoffs from session markdown."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .config import CLAUDE_MODEL, EXTRACTORS_DIR, HANDOFFS_DIR

logger = logging.getLogger(__name__)

PROMPT_PATH = EXTRACTORS_DIR / "handoff.md"
SCHEMA_PATH = EXTRACTORS_DIR / "handoff_schema.json"
EXTRACT_SCRIPT = EXTRACTORS_DIR / "extract.mjs"


def _json_to_markdown(data: dict[str, Any]) -> str:
    """Convert validated JSON handoff output into markdown with frontmatter."""
    frontmatter = {
        "session_id": data["session_id"],
        "date": data["date"],
        "duration": data.get("duration", ""),
        "model": data.get("model", ""),
        "headline": data["headline"],
        "projects": data.get("projects", []),
        "keywords": data.get("keywords", []),
        "substance": data["substance"],
    }

    lines = ["---", yaml.safe_dump(frontmatter, sort_keys=False).strip(), "---", ""]
    lines.append(f"# {data['headline']}")

    if data["substance"] == 0:
        lines.extend(["", "No meaningful work."])
    else:
        if data.get("what_happened"):
            lines.extend(["", "## What Happened", str(data["what_happened"]).strip()])
        if data.get("insights"):
            lines.extend(["", "## Insights", str(data["insights"]).strip()])
        if data.get("key_files") and data["substance"] >= 2:
            lines.extend(["", "## Key Files", str(data["key_files"]).strip()])
        if data.get("open_threads"):
            lines.extend(["", "## Open Threads", str(data["open_threads"]).strip()])

    return "\n".join(lines).rstrip() + "\n"


def _validate_handoff(data: dict[str, Any]) -> str | None:
    """Validate essential extracted fields before writing handoff output."""
    session_id = str(data.get("session_id", ""))
    if len(session_id) < 4:
        return f"invalid session_id: {session_id!r}"

    date_str = str(data.get("date", ""))
    try:
        datetime.fromisoformat(date_str)
    except ValueError:
        return f"invalid date: {date_str!r}"

    headline = str(data.get("headline", "")).strip()
    if len(headline) < 5:
        return f"headline too short: {headline!r}"

    substance = data.get("substance")
    if substance not in (0, 1, 2):
        return f"invalid substance: {substance!r}"

    return None


def handoff_json_to_markdown(data: dict[str, Any]) -> str:
    """Public wrapper to render extracted handoff JSON as markdown."""
    return _json_to_markdown(data)


def validate_handoff(data: dict[str, Any]) -> str | None:
    """Public wrapper for handoff payload validation."""
    return _validate_handoff(data)


def extract_handoff(session_md_path: Path, max_retries: int = 1) -> str | None:
    """Extract a structured handoff markdown document from a session markdown file."""
    if not EXTRACT_SCRIPT.exists():
        raise FileNotFoundError(f"Extraction script not found: {EXTRACT_SCRIPT}")
    if not PROMPT_PATH.exists():
        raise FileNotFoundError(f"Prompt file not found: {PROMPT_PATH}")
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Schema file not found: {SCHEMA_PATH}")

    try:
        session_content = session_md_path.read_text(encoding="utf-8")
    except OSError:
        logger.exception("Failed to read session markdown: %s", session_md_path)
        return None

    for attempt in range(max_retries + 1):
        env = os.environ.copy()
        env.setdefault("EYWA_CLAUDE_MODEL", CLAUDE_MODEL)

        try:
            result = subprocess.run(
                ["node", str(EXTRACT_SCRIPT)],
                input=session_content,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
                cwd=str(EXTRACTORS_DIR),
                env=env,
            )
        except subprocess.TimeoutExpired:
            logger.warning("Extraction timed out on attempt %s", attempt + 1)
            continue
        except FileNotFoundError:
            logger.error("Node.js was not found in PATH")
            return None

        if result.returncode != 0:
            stderr = result.stderr.strip().splitlines()[:4]
            logger.warning(
                "extract.mjs failed on attempt %s (code=%s): %s",
                attempt + 1,
                result.returncode,
                " | ".join(stderr),
            )
            continue

        output = result.stdout.strip()
        if not output:
            logger.warning("extract.mjs returned empty output on attempt %s", attempt + 1)
            continue

        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            logger.warning("extract.mjs returned invalid JSON on attempt %s", attempt + 1)
            continue

        validation_error = _validate_handoff(payload)
        if validation_error:
            logger.warning("Extraction validation failed on attempt %s: %s", attempt + 1, validation_error)
            continue

        return _json_to_markdown(payload)

    logger.error("All extraction attempts failed for %s", session_md_path)
    return None


def save_handoff(
    handoff_content: str,
    session_md_path: Path,
    output_dir: Path | None = None,
) -> Path | None:
    """Persist handoff markdown to ``YYYY/MM/DD/<session_id>.md``."""
    del session_md_path  # Not used for naming; retained for compatibility.

    from .parse import parse_frontmatter

    frontmatter, _ = parse_frontmatter(handoff_content)
    session_id = str(frontmatter.get("session_id", ""))
    date_str = str(frontmatter.get("date", ""))

    if not session_id or not date_str:
        logger.error("Cannot save handoff: missing session_id/date in frontmatter")
        return None

    try:
        year, month, day = date_str.split("-")
    except ValueError:
        logger.error("Cannot save handoff: invalid date format %r", date_str)
        return None

    base_dir = output_dir or HANDOFFS_DIR
    target_dir = base_dir / year / month / day
    target_dir.mkdir(parents=True, exist_ok=True)

    output_path = target_dir / f"{session_id}.md"
    temp_path = output_path.with_suffix(".tmp")

    try:
        temp_path.write_text(handoff_content, encoding="utf-8")
        temp_path.replace(output_path)
    except OSError:
        logger.exception("Failed to write handoff to %s", output_path)
        temp_path.unlink(missing_ok=True)
        return None

    return output_path
