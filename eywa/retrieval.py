"""Keyword-based handoff retrieval."""

from __future__ import annotations

import json
import re
from datetime import datetime
from math import log, sqrt
from pathlib import Path
from typing import Any

from .config import HANDOFFS_DIR, INDEX_PATH

STOPWORDS = {
    "let",
    "lets",
    "let's",
    "continue",
    "work",
    "on",
    "the",
    "a",
    "an",
    "with",
    "to",
    "for",
    "of",
    "in",
    "and",
    "or",
    "is",
    "are",
    "was",
    "what",
    "how",
    "why",
    "when",
    "where",
    "do",
    "does",
    "did",
    "have",
    "has",
    "had",
    "be",
    "been",
    "being",
    "this",
    "that",
    "these",
    "those",
    "my",
    "your",
    "our",
    "their",
    "me",
    "you",
    "we",
    "they",
    "i",
    "about",
    "know",
    "think",
    "can",
    "could",
    "would",
    "should",
    "will",
    "shall",
    "all",
    "get",
    "show",
    "find",
    "search",
    "list",
    "give",
    "need",
    "want",
    "some",
    "any",
    "just",
    "more",
    "also",
    "very",
    "much",
    "here",
    "there",
}

KNOWN_SHORT = {"hr", "ai", "ml", "kb", "qa", "ui", "ux", "api", "cli", "os", "mcp", "llm", "gpu", "3d"}


class EywaRetrieval:
    """Retrieval engine for ranking handoffs by keyword relevance and recency."""

    def __init__(self, index_path: Path | None = None, handoffs_dir: Path | None = None) -> None:
        self.index_path = index_path or INDEX_PATH
        self.handoffs_dir = handoffs_dir or HANDOFFS_DIR
        self._index: dict[str, Any] | None = None
        self._index_mtime = 0.0

    @property
    def index(self) -> dict[str, Any]:
        """Load and cache the index file, invalidating cache on mtime changes."""
        if not self.index_path.exists():
            raise FileNotFoundError(f"Handoff index not found: {self.index_path}")

        current_mtime = self.index_path.stat().st_mtime
        if self._index is None or current_mtime != self._index_mtime:
            self._index = json.loads(self.index_path.read_text(encoding="utf-8"))
            self._index_mtime = current_mtime

        return self._index

    def extract_keywords(self, text: str) -> list[str]:
        """Extract normalized query keywords while removing common stopwords."""
        words = re.split(r"[^a-zA-Z0-9-]+", text.lower())
        return [w for w in words if w and (len(w) > 2 or w in KNOWN_SHORT) and w not in STOPWORDS]

    def score_handoffs(self, keywords: list[str], days_back: int) -> list[tuple[str, float]]:
        """Score handoffs using project and keyword IDF with recency decay."""
        scores: dict[str, float] = {}
        by_project = self.index.get("by_project", {})
        by_keyword = self.index.get("by_keyword", {})
        handoffs = self.index.get("handoffs", {})
        n_total = max(len(handoffs), 1)

        for keyword in keywords:
            for project_name, session_ids in by_project.items():
                if keyword in project_name.lower():
                    idf = max(0.0, log(n_total / max(len(session_ids), 1)))
                    for session_id in session_ids:
                        scores[session_id] = scores.get(session_id, 0.0) + 3.0 * idf

            for indexed_keyword, session_ids in by_keyword.items():
                if keyword == indexed_keyword or keyword in indexed_keyword:
                    idf = max(0.0, log(n_total / max(len(session_ids), 1)))
                    for session_id in session_ids:
                        scores[session_id] = scores.get(session_id, 0.0) + 2.0 * idf

        today = datetime.now().date()
        result: list[tuple[str, float]] = []

        for session_id, score in scores.items():
            handoff = handoffs.get(session_id, {})
            if handoff.get("substance", 1) == 0:
                continue

            date_str = handoff.get("date")
            if isinstance(date_str, str) and date_str:
                try:
                    age_days = (today - datetime.fromisoformat(date_str).date()).days + 1
                    if age_days <= days_back:
                        score *= 1 + 1 / sqrt(max(age_days, 1))
                    else:
                        score *= 0.5 ** ((age_days - days_back) / 7)
                except ValueError:
                    pass

            duration = handoff.get("duration_minutes", 0)
            if isinstance(duration, (int, float)) and duration > 0:
                score *= 1 + 0.1 * log(duration + 1)

            result.append((session_id, score))

        return sorted(result, key=lambda item: -item[1])

    def get_recent(self, max_handoffs: int, days_back: int) -> list[str]:
        """Return most recent session IDs with substance >= 1 within ``days_back``."""
        handoffs = self.index.get("handoffs", {})
        today = datetime.now().date()

        candidates: list[tuple[str, str]] = []
        for session_id, handoff in handoffs.items():
            if handoff.get("substance", 1) == 0:
                continue

            date_str = handoff.get("date")
            if not isinstance(date_str, str) or not date_str:
                continue

            try:
                age = (today - datetime.fromisoformat(date_str).date()).days
            except ValueError:
                continue

            if age <= days_back:
                candidates.append((session_id, date_str))

        candidates.sort(key=lambda item: item[1], reverse=True)
        return [session_id for session_id, _ in candidates[:max_handoffs]]

    def load_handoff_content(self, session_id: str) -> str | None:
        """Load markdown handoff content for a session ID."""
        handoff = self.index.get("handoffs", {}).get(session_id, {})
        date_str = handoff.get("date", "")
        if not date_str:
            return None

        try:
            year, month, day = str(date_str).split("-")
        except ValueError:
            return None

        path = self.handoffs_dir / year / month / day / f"{session_id}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def get_handoffs(
        self,
        query: str | None = None,
        days_back: int = 14,
        max_handoffs: int = 3,
    ) -> str:
        """Return formatted handoff context text for retrieval requests."""
        if not self.index.get("handoffs"):
            return "No past sessions found."

        if query:
            keywords = self.extract_keywords(query)
            ranked = self.score_handoffs(keywords, days_back) if keywords else []
            session_ids = [session_id for session_id, _ in ranked[:max_handoffs]]
            if not session_ids:
                session_ids = self.get_recent(max_handoffs, days_back)
        else:
            session_ids = self.get_recent(max_handoffs, days_back)

        if not session_ids:
            return "No past sessions found."

        contents: list[str] = []
        for session_id in session_ids:
            content = self.load_handoff_content(session_id)
            if not content:
                continue

            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    content = parts[2].strip()
            contents.append(content)

        if not contents:
            return "No past sessions found."

        count = len(contents)
        header = f"## Eywa: {count} past session{'s' if count != 1 else ''}\n"
        return header + "\n\n---\n\n".join(contents)
