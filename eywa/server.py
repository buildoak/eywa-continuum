"""Eywa MCP server entry point."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .config import HANDOFFS_DIR, INDEX_PATH, LOG_LEVEL, ensure_data_dirs
from .detect_session import detect_session
from .extract import extract_handoff, save_handoff
from .index import handoff_to_index_entry, update_index
from .parse import parse_handoff
from .retrieval import EywaRetrieval
from .session_convert import jsonl_to_markdown

logger = logging.getLogger("eywa-mcp")
server = Server("eywa-mcp")
_retrieval: EywaRetrieval | None = None


def _setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def get_retrieval() -> EywaRetrieval:
    """Return a singleton retrieval engine instance."""
    global _retrieval
    if _retrieval is None:
        _retrieval = EywaRetrieval()
    return _retrieval


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Declare tools exposed by this MCP server."""
    return [
        Tool(
            name="eywa_get",
            description=(
                "Retrieve past session handoffs for context continuity. "
                "Without a query, returns recent sessions; with a query, returns top keyword matches."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords or topic for retrieval.",
                    },
                    "days_back": {
                        "type": "integer",
                        "default": 14,
                        "description": "How far back to search.",
                    },
                    "max_handoffs": {
                        "type": "integer",
                        "default": 3,
                        "description": "Maximum number of handoffs to return (max 5).",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="eywa_extract",
            description=(
                "Extract and persist a handoff from the active session or a specific session_id."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Optional full session UUID. Auto-detected if omitted.",
                    }
                },
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
    """Dispatch incoming tool call requests."""
    payload = arguments or {}

    if name == "eywa_get":
        return await _handle_eywa_get(payload)
    if name == "eywa_extract":
        return await _handle_eywa_extract(payload)

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def _handle_eywa_get(arguments: dict[str, Any]) -> list[TextContent]:
    try:
        retrieval = get_retrieval()
        result = retrieval.get_handoffs(
            query=arguments.get("query"),
            days_back=int(arguments.get("days_back", 14)),
            max_handoffs=min(int(arguments.get("max_handoffs", 3)), 5),
        )
        return [TextContent(type="text", text=result)]
    except FileNotFoundError as exc:
        return [TextContent(type="text", text=f"Eywa is not initialized yet: {exc}")]
    except (TypeError, ValueError) as exc:
        return [TextContent(type="text", text=f"Invalid arguments: {exc}")]
    except Exception as exc:
        logger.exception("Unexpected error while running eywa_get")
        return [TextContent(type="text", text=f"Error: {exc}")]


async def _handle_eywa_extract(arguments: dict[str, Any]) -> list[TextContent]:
    """Extract a handoff from current or specified session."""
    try:
        explicit_session = arguments.get("session_id")
        jsonl_path, detection_error = detect_session(explicit_session)
        if not jsonl_path:
            return [
                TextContent(
                    type="text",
                    text=f"Session detection failed: {detection_error or 'unknown error'}",
                )
            ]

        session_uuid = jsonl_path.stem
        short_id = session_uuid[:8]

        try:
            retrieval = get_retrieval()
            current_entry = retrieval.index.get("handoffs", {}).get(short_id)
            if current_entry:
                handoff_date = str(current_entry.get("date", ""))
                try:
                    year, month, day = handoff_date.split("-")
                    handoff_path = HANDOFFS_DIR / year / month / day / f"{short_id}.md"
                    if handoff_path.exists() and jsonl_path.stat().st_mtime <= handoff_path.stat().st_mtime:
                        return [
                            TextContent(
                                type="text",
                                text=f"Handoff already exists for {short_id} (session unchanged)",
                            )
                        ]
                except ValueError:
                    logger.warning("Invalid date in index for %s: %r", short_id, handoff_date)
        except FileNotFoundError:
            # First run: no index exists yet.
            pass

        markdown = jsonl_to_markdown(jsonl_path)
        if not markdown:
            return [
                TextContent(type="text", text=f"Session {short_id} has no extractable content")
            ]

        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as temp:
                temp.write(markdown)
                temp_path = Path(temp.name)

            handoff_content = await asyncio.to_thread(extract_handoff, temp_path)
            if not handoff_content:
                return [
                    TextContent(
                        type="text",
                        text=f"Extraction failed for {short_id}. Check Node and Claude SDK setup.",
                    )
                ]

            handoff_path = await asyncio.to_thread(save_handoff, handoff_content, temp_path, HANDOFFS_DIR)
            if not handoff_path:
                return [TextContent(type="text", text=f"Failed to save handoff for {short_id}")]
        finally:
            if temp_path:
                temp_path.unlink(missing_ok=True)

        parsed = await asyncio.to_thread(parse_handoff, handoff_path)
        entry = handoff_to_index_entry(parsed)

        index_session_id = str(parsed.get("session_id", short_id))
        if index_session_id != short_id:
            logger.warning(
                "Session ID mismatch while indexing: source=%s parsed=%s; using parsed",
                short_id,
                index_session_id,
            )

        updated = await asyncio.to_thread(update_index, entry, index_session_id, INDEX_PATH)
        if not updated:
            return [TextContent(type="text", text=f"Handoff saved but indexing failed for {index_session_id}")]

        return [
            TextContent(
                type="text",
                text=f"Handoff extracted: {handoff_path.name}\n\n{handoff_content}",
            )
        ]
    except Exception as exc:
        logger.exception("Unexpected error while running eywa_extract")
        return [TextContent(type="text", text=f"Extraction error: {exc}")]


async def main() -> None:
    """Run the MCP stdio server loop."""
    _setup_logging()
    ensure_data_dirs()
    logger.info("Starting eywa-mcp server")

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def cli() -> None:
    """CLI entry point for ``eywa-mcp``."""
    asyncio.run(main())


if __name__ == "__main__":
    cli()
