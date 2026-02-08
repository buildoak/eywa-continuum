"""Batch index Claude Code sessions with OpenRouter extraction."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

import aiohttp

from .config import (
    BATCH_CONCURRENCY,
    BATCH_DELAY,
    HANDOFFS_DIR,
    INDEX_PATH,
    LOG_LEVEL,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
    SESSIONS_DIR,
    ensure_data_dirs,
)
from .extract import (
    PROMPT_PATH,
    SCHEMA_PATH,
    handoff_json_to_markdown,
    save_handoff,
    validate_handoff,
)
from .index import handoff_to_index_entry, update_index
from .parse import parse_frontmatter, parse_handoff
from .session_convert import parse_jsonl_to_session, session_to_markdown

logger = logging.getLogger("eywa-batch")

MIN_TURNS = 3
MIN_CONTENT_CHARS = 400
CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _load_indexed_session_ids(index_path: Path) -> set[str]:
    if not index_path.exists():
        return set()

    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read existing index at %s; proceeding as empty", index_path)
        return set()

    handoffs = payload.get("handoffs", {})
    if not isinstance(handoffs, dict):
        return set()
    return {str(session_id) for session_id in handoffs.keys() if session_id}


def _list_session_files(sessions_dir: Path) -> list[Path]:
    if not sessions_dir.is_dir():
        return []

    candidates = [path for path in sessions_dir.rglob("*.jsonl") if path.is_file()]
    candidates.sort(key=lambda path: (path.stat().st_mtime, str(path)))
    return candidates


def _short_session_id(path: Path) -> str:
    return path.stem[:8]


def _conversation_stats(session: dict[str, Any]) -> tuple[int, int]:
    turns = session.get("turns", [])
    turn_count = len(turns)
    content_chars = 0

    for turn in turns:
        content_chars += len(str(turn.get("user", "")))
        content_chars += len(str(turn.get("assistant", "")))

    return turn_count, content_chars


def _build_user_message(session_markdown: str, schema_text: str) -> str:
    return (
        "Return only a JSON object that strictly matches this schema.\n"
        "Do not include markdown fences or any explanatory text.\n\n"
        "JSON schema:\n"
        f"{schema_text.strip()}\n\n"
        "Session transcript markdown:\n"
        f"{session_markdown}"
    )


def _parse_response_json(raw_text: str) -> dict[str, Any] | None:
    text = raw_text.strip()
    if not text:
        return None

    cleaned = CODE_FENCE_RE.sub("", text).strip()
    for candidate in (cleaned, text):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    first = cleaned.find("{")
    last = cleaned.rfind("}")
    if first == -1 or last == -1 or last <= first:
        return None

    try:
        parsed = json.loads(cleaned[first : last + 1])
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        return None

    return None


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        nested = content.get("content")
        if isinstance(nested, str):
            return nested
        return ""

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
                continue
            nested = item.get("content")
            if isinstance(nested, str):
                parts.append(nested)
        return "\n".join(parts).strip()

    return ""


async def _extract_with_openrouter(
    http_session: aiohttp.ClientSession,
    model: str,
    api_key: str,
    system_prompt: str,
    user_message: str,
) -> tuple[dict[str, Any] | None, str | None]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }

    try:
        async with http_session.post(OPENROUTER_URL, json=body, headers=headers) as response:
            raw_text = await response.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        return None, str(exc)

    if response.status >= 400:
        snippet = raw_text.strip().replace("\n", " ")
        return None, f"OpenRouter HTTP {response.status}: {snippet[:280]}"

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return None, "OpenRouter returned a non-JSON response"

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None, "OpenRouter response missing choices"

    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        return None, "OpenRouter response contained an invalid choice payload"

    message = first_choice.get("message", {})
    if not isinstance(message, dict):
        return None, "OpenRouter response missing message payload"

    text = _message_content_to_text(message.get("content", ""))
    if not text:
        return None, "OpenRouter returned an empty message"

    payload = _parse_response_json(text)
    if not payload:
        return None, "OpenRouter returned non-JSON or empty output"

    return payload, None


async def _print_with_lock(lock: asyncio.Lock, message: str) -> None:
    async with lock:
        print(message)


async def _process_session(
    offset: int,
    total: int,
    session_path: Path,
    *,
    dry_run: bool,
    delay: float,
    model: str,
    api_key: str | None,
    instructions: str,
    schema_text: str,
    http_session: aiohttp.ClientSession | None,
    output_lock: asyncio.Lock,
    index_lock: asyncio.Lock,
    semaphore: asyncio.Semaphore,
    rate_limit_lock: asyncio.Lock,
    rate_limit_state: dict[str, float],
) -> tuple[int, int, int]:
    async with semaphore:
        sid = _short_session_id(session_path)
        await _print_with_lock(output_lock, f"[{offset}/{total}] {sid} <- {session_path}")

        try:
            session = await asyncio.to_thread(parse_jsonl_to_session, session_path)
        except OSError as exc:
            await _print_with_lock(output_lock, f"  FAILED (read error): {exc}")
            return 0, 0, 1

        turn_count, content_chars = _conversation_stats(session)
        if turn_count < MIN_TURNS:
            await _print_with_lock(
                output_lock, f"  SKIP short session ({turn_count} turns; minimum {MIN_TURNS})"
            )
            return 0, 1, 0
        if content_chars < MIN_CONTENT_CHARS:
            await _print_with_lock(
                output_lock,
                f"  SKIP trivial session ({content_chars} chars; minimum {MIN_CONTENT_CHARS})",
            )
            return 0, 1, 0

        markdown = await asyncio.to_thread(session_to_markdown, session)
        frontmatter, _ = parse_frontmatter(markdown)

        expected_session_id = str(frontmatter.get("session_id", sid))
        expected_date = str(frontmatter.get("date", ""))
        if not expected_date or expected_date == "unknown":
            await _print_with_lock(output_lock, "  FAILED (session date unavailable)")
            return 0, 0, 1

        if dry_run:
            await _print_with_lock(
                output_lock,
                "  DRY RUN would extract and index "
                f"(turns={turn_count}, chars={content_chars}, date={expected_date})",
            )
            return 1, 0, 0

        if not http_session or not api_key:
            await _print_with_lock(output_lock, "  FAILED (OpenRouter client unavailable)")
            return 0, 0, 1

        user_message = _build_user_message(markdown, schema_text)

        if delay > 0:
            async with rate_limit_lock:
                now = asyncio.get_running_loop().time()
                ready_at = rate_limit_state.get("ready_at", 0.0)
                if ready_at > now:
                    await asyncio.sleep(ready_at - now)
                rate_limit_state["ready_at"] = asyncio.get_running_loop().time() + delay

        payload, extract_error = await _extract_with_openrouter(
            http_session=http_session,
            model=model,
            api_key=api_key,
            system_prompt=instructions,
            user_message=user_message,
        )
        if extract_error or not payload:
            await _print_with_lock(output_lock, f"  FAILED (OpenRouter extraction): {extract_error}")
            return 0, 0, 1

        if payload.get("session_id") != expected_session_id:
            logger.warning(
                "Normalizing session_id from %r to %r for %s",
                payload.get("session_id"),
                expected_session_id,
                session_path,
            )
            payload["session_id"] = expected_session_id

        if payload.get("date") != expected_date:
            logger.warning(
                "Normalizing date from %r to %r for %s",
                payload.get("date"),
                expected_date,
                session_path,
            )
            payload["date"] = expected_date

        if not payload.get("duration"):
            payload["duration"] = frontmatter.get("duration", "")
        if not payload.get("model"):
            payload["model"] = frontmatter.get("model", "")

        validation_error = validate_handoff(payload)
        if validation_error:
            await _print_with_lock(output_lock, f"  FAILED (validation): {validation_error}")
            return 0, 0, 1

        handoff_md = handoff_json_to_markdown(payload)
        handoff_path = await asyncio.to_thread(save_handoff, handoff_md, session_path, HANDOFFS_DIR)
        if not handoff_path:
            await _print_with_lock(output_lock, "  FAILED (could not save handoff markdown)")
            return 0, 0, 1

        try:
            parsed = await asyncio.to_thread(parse_handoff, handoff_path)
        except OSError as exc:
            await _print_with_lock(output_lock, f"  FAILED (parse saved handoff): {exc}")
            return 0, 0, 1

        entry = handoff_to_index_entry(parsed)
        index_session_id = str(parsed.get("session_id", expected_session_id))
        async with index_lock:
            indexed = await asyncio.to_thread(update_index, entry, index_session_id, INDEX_PATH)
        if not indexed:
            await _print_with_lock(output_lock, "  FAILED (index update)")
            return 0, 0, 1

        await _print_with_lock(output_lock, f"  OK saved {handoff_path}")
        return 1, 0, 0


async def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="eywa-batch",
        description="Batch index Claude Code sessions using OpenRouter chat completions.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show planned work without calling OpenRouter."
    )
    parser.add_argument("--delay", type=float, default=BATCH_DELAY, help="Delay between API calls (seconds).")
    parser.add_argument("--max", dest="max_sessions", type=int, default=None, help="Process at most N sessions.")
    parser.add_argument("--reindex", action="store_true", help="Re-process all sessions even if indexed.")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=BATCH_CONCURRENCY,
        help="Number of sessions to process concurrently (1-20).",
    )

    args = parser.parse_args(argv)
    if args.delay < 0:
        parser.error("--delay must be >= 0")
    if args.max_sessions is not None and args.max_sessions < 1:
        parser.error("--max must be >= 1")
    if args.concurrency < 1 or args.concurrency > 20:
        parser.error("--concurrency must be between 1 and 20")

    _setup_logging()
    ensure_data_dirs()

    if not PROMPT_PATH.exists():
        print(f"Extractor prompt not found: {PROMPT_PATH}")
        return 2
    if not SCHEMA_PATH.exists():
        print(f"Extractor schema not found: {SCHEMA_PATH}")
        return 2

    try:
        instructions = PROMPT_PATH.read_text(encoding="utf-8")
        schema_text = SCHEMA_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"Failed to load extractor assets: {exc}")
        return 2

    all_sessions = _list_session_files(SESSIONS_DIR)
    if not all_sessions:
        print(f"No session JSONL files found in {SESSIONS_DIR}")
        return 0

    indexed_session_ids = set() if args.reindex else _load_indexed_session_ids(INDEX_PATH)

    queued: list[Path] = []
    skipped_indexed = 0
    for path in all_sessions:
        sid = _short_session_id(path)
        if not args.reindex and sid in indexed_session_ids:
            skipped_indexed += 1
            continue
        queued.append(path)

    if args.max_sessions is not None:
        queued = queued[: args.max_sessions]

    if not queued:
        print("Nothing to process. All discovered sessions are already indexed.")
        print(f"Scanned: {len(all_sessions)} | Already indexed: {skipped_indexed}")
        return 0

    if not args.dry_run and not OPENROUTER_API_KEY:
        print("OPENROUTER_API_KEY is not set. Set it before running eywa-batch.")
        return 2

    print(
        f"Scanning complete. Found {len(all_sessions)} sessions; queued {len(queued)} "
        f"({'reindex' if args.reindex else 'unindexed'} mode)."
    )
    print(
        f"Model: {OPENROUTER_MODEL} | Dry run: {args.dry_run} | Delay: {args.delay}s | "
        f"Concurrency: {args.concurrency} | Output dir: {HANDOFFS_DIR}"
    )

    output_lock = asyncio.Lock()
    index_lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(args.concurrency)
    rate_limit_lock = asyncio.Lock()
    rate_limit_state = {"ready_at": 0.0}

    async def _run_tasks(http_session: aiohttp.ClientSession | None) -> list[tuple[int, int, int]]:
        tasks = [
            asyncio.create_task(
                _process_session(
                    offset=offset,
                    total=len(queued),
                    session_path=session_path,
                    dry_run=args.dry_run,
                    delay=args.delay,
                    model=OPENROUTER_MODEL,
                    api_key=OPENROUTER_API_KEY,
                    instructions=instructions,
                    schema_text=schema_text,
                    http_session=http_session,
                    output_lock=output_lock,
                    index_lock=index_lock,
                    semaphore=semaphore,
                    rate_limit_lock=rate_limit_lock,
                    rate_limit_state=rate_limit_state,
                )
            )
            for offset, session_path in enumerate(queued, start=1)
        ]
        return await asyncio.gather(*tasks)

    if args.dry_run:
        results = await _run_tasks(None)
    else:
        timeout = aiohttp.ClientTimeout(total=300)
        async with aiohttp.ClientSession(timeout=timeout) as http_session:
            results = await _run_tasks(http_session)

    processed = sum(processed_count for processed_count, _, _ in results)
    skipped_small = sum(skipped_count for _, skipped_count, _ in results)
    failed = sum(failed_count for _, _, failed_count in results)

    print("\nBatch indexing complete.")
    print(
        f"Scanned={len(all_sessions)} Queued={len(queued)} Processed={processed} "
        f"SkippedIndexed={skipped_indexed} SkippedSmall={skipped_small} Failed={failed}"
    )

    return 1 if failed else 0


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
