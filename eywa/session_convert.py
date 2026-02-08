"""Convert Claude Code session JSONL files to markdown."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import TIMEZONE

logger = logging.getLogger(__name__)

NOISE_TYPES = {"file-history-snapshot", "queue-operation", "progress"}


def _timezone() -> timezone | ZoneInfo:
    try:
        return ZoneInfo(TIMEZONE)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown timezone %r, falling back to UTC", TIMEZONE)
        return timezone.utc


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(_timezone())
    except ValueError:
        return None


def _fmt_duration(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return "0m"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            continue

        block_type = item.get("type")
        if block_type == "text" and item.get("text"):
            parts.append(str(item["text"]))
        elif block_type == "tool_use":
            name = item.get("name", "tool")
            parts.append(f"[tool: {name}]")

    return "\n\n".join(part.strip() for part in parts if part and part.strip())


def _truncate(text: str, limit: int = 100_000, preview: int = 5_000) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:preview]}\n\n[... truncated from {len(text)} chars]"


def parse_jsonl_to_session(path: Path) -> dict[str, Any]:
    """Parse a Claude Code JSONL session into a normalized session dictionary."""
    records: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # Live sessions can have partially-written trailing lines.
                continue

            if record.get("type") in NOISE_TYPES:
                continue
            records.append(record)

    session_id = path.stem
    summary: str | None = None
    models: set[str] = set()
    timestamps: list[str] = []

    turns: list[dict[str, Any]] = []
    current_turn: dict[str, Any] | None = None

    for record in records:
        if not session_id and record.get("sessionId"):
            session_id = str(record["sessionId"])

        if record.get("timestamp"):
            timestamps.append(str(record["timestamp"]))

        rtype = record.get("type")

        if rtype == "summary":
            summary = record.get("summary")
            continue

        message = record.get("message") or {}

        if rtype == "user":
            user_text = _extract_text(message.get("content"))
            # Skip interrupted request markers.
            if "[Request interrupted by user]" in user_text:
                continue

            if current_turn:
                turns.append(current_turn)
            current_turn = {
                "user": user_text,
                "assistant": "",
                "timestamp_start": record.get("timestamp"),
                "timestamp_end": None,
                "model": None,
            }
            continue

        if rtype == "assistant":
            model = message.get("model")
            if isinstance(model, str) and model and not model.startswith("<"):
                models.add(model)

            assistant_text = _extract_text(message.get("content"))
            if current_turn is None:
                current_turn = {
                    "user": "",
                    "assistant": assistant_text,
                    "timestamp_start": record.get("timestamp"),
                    "timestamp_end": record.get("timestamp"),
                    "model": model,
                }
            else:
                if assistant_text:
                    if current_turn["assistant"]:
                        current_turn["assistant"] += "\n\n"
                    current_turn["assistant"] += assistant_text
                current_turn["timestamp_end"] = record.get("timestamp")
                current_turn["model"] = model

    if current_turn:
        turns.append(current_turn)

    ts_start = min(timestamps) if timestamps else None
    ts_end = max(timestamps) if timestamps else None

    duration_seconds: float | None = None
    start_dt = _parse_ts(ts_start)
    end_dt = _parse_ts(ts_end)
    if start_dt and end_dt:
        duration_seconds = max((end_dt - start_dt).total_seconds(), 0.0)

    return {
        "session_id": str(session_id),
        "summary": summary,
        "turns": turns,
        "timestamp_start": ts_start,
        "timestamp_end": ts_end,
        "duration_seconds": duration_seconds,
        "models_used": sorted(models),
    }


def session_to_markdown(session: dict[str, Any]) -> str:
    """Render a normalized session dictionary to markdown."""
    turns = session.get("turns", [])
    start_dt = _parse_ts(session.get("timestamp_start"))
    end_dt = _parse_ts(session.get("timestamp_end"))

    session_id = str(session.get("session_id", "unknown"))[:8]
    model = session.get("models_used", ["unknown"])
    model_name = model[0] if model else "unknown"

    lines: list[str] = [
        "---",
        f"session_id: {session_id}",
        f"date: {start_dt.strftime('%Y-%m-%d') if start_dt else 'unknown'}",
        f"start: {start_dt.strftime('%H:%M') if start_dt else 'unknown'}",
        f"end: {end_dt.strftime('%H:%M') if end_dt else 'unknown'}",
        f"duration: {_fmt_duration(session.get('duration_seconds'))}",
        f"model: {model_name}",
        f"turns: {len(turns)}",
        "---",
        "",
    ]

    title = session.get("summary") or "Session Handoff Source"
    title = str(title).strip()[:80] or "Session Handoff Source"
    lines.append(f"# Session: {title}")
    lines.append("")
    lines.append("## Conversation")
    lines.append("")

    for turn in turns:
        user_text = _truncate(str(turn.get("user", "")))
        assistant_text = _truncate(str(turn.get("assistant", "")))

        start_ts = _parse_ts(turn.get("timestamp_start"))
        end_ts = _parse_ts(turn.get("timestamp_end") or turn.get("timestamp_start"))

        if user_text:
            lines.append(f"### [{start_ts.strftime('%H:%M') if start_ts else '??:??'}] User")
            lines.append(user_text)
            lines.append("")

        if assistant_text:
            lines.append(f"### [{end_ts.strftime('%H:%M') if end_ts else '??:??'}] Claude")
            lines.append(assistant_text)
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def jsonl_to_markdown(jsonl_path: Path) -> str | None:
    """Convert a live JSONL session file to markdown.

    Returns markdown text, or ``None`` if the file is missing, empty, or invalid.
    """
    path = Path(jsonl_path)
    if not path.is_file():
        return None

    try:
        session = parse_jsonl_to_session(path)
    except OSError:
        logger.exception("Failed to read JSONL session file: %s", path)
        return None

    if not session.get("turns"):
        return None
    return session_to_markdown(session)
