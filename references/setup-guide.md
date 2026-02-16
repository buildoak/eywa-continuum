# Eywa Setup Guide

First-time installation and configuration for cross-session memory.

## Architecture Overview

Eywa has two processing stages with different model requirements:

| Stage | Command | Model Provider | Purpose |
|-------|---------|---------------|---------|
| Batch (one-time) | `eywa-batch` | OpenRouter | Bulk-index historical sessions. Needs a cheap, fast model for hundreds of API calls. |
| Runtime (ongoing) | `eywa extract` | Anthropic SDK | Extract handoffs during live sessions. Uses Claude via Node.js Agent SDK. |
| Retrieval | `eywa get` | None | Local keyword search over the index. No API calls. |

**Why two providers?** Batch processing may hit hundreds of sessions -- using OpenRouter with a cheap model (Gemini Flash) keeps costs minimal. Runtime extraction happens once per session and runs through the Claude Agent SDK that is already available in the Claude Code environment.

## Prerequisites

- Python 3.10+
- Node.js 18+
- `ANTHROPIC_API_KEY` set (for runtime `eywa extract`)
- `OPENROUTER_API_KEY` set (for `eywa-batch` only)

## Step-by-Step First Run

### 1. Install

```bash
cd /path/to/eywa-mcp
./setup.sh
```

The setup script is idempotent -- safe to run multiple times. It installs the Python package (editable mode) and Node.js extractor dependencies, then verifies that `eywa` and `eywa-mcp` commands are on PATH.

### 2. Configure Environment

Copy `.env.example` and set your keys:

```bash
cp .env.example .env
# Edit .env -- at minimum set OPENROUTER_API_KEY for batch indexing
```

### 3. Preview Batch Indexing

```bash
eywa-batch --dry-run
```

Shows how many sessions would be processed without calling any APIs. Useful to verify session discovery and estimate scope.

### 4. Run Batch Index

```bash
eywa-batch
```

Processes all unindexed sessions through OpenRouter, creates handoff documents, and builds the search index. First run on a large session history may take several minutes.

### 5. Verify

```bash
eywa get                    # Returns recent sessions
eywa get "mcp routing"      # Keyword search
```

If these return handoff content, setup is complete.

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `EYWA_DATA_DIR` | `~/.eywa` | Root directory for handoffs and index |
| `EYWA_SESSIONS_DIR` | `~/.claude/projects` | Where Claude Code session JSONL files live |
| `EYWA_TASKS_DIR` | `~/.claude/tasks` | Task artifacts for PID-based session detection |
| `EYWA_CLAUDE_MODEL` | `sonnet` | Model for runtime extraction (Anthropic SDK, `eywa extract`) |
| `EYWA_OPENROUTER_MODEL` | `google/gemini-3-flash-preview` | Model for batch indexing (OpenRouter, `eywa-batch`) |
| `EYWA_BATCH_DELAY` | `0.5` | Seconds between batch API calls (rate limiting) |
| `EYWA_BATCH_CONCURRENCY` | `5` | Concurrent workers for batch processing (1-20) |
| `EYWA_TIMEZONE` | `UTC` | Timezone for session timestamp rendering |
| `EYWA_LOG_LEVEL` | `INFO` | Logging level: DEBUG, INFO, WARNING, ERROR |
| `OPENROUTER_API_KEY` | (none) | Required for `eywa-batch` |
| `ANTHROPIC_API_KEY` | (none) | Required for `eywa extract` (Claude Agent SDK) |

## Batch Command Reference

```
eywa-batch [OPTIONS]

Options:
  --dry-run              Preview work without calling OpenRouter
  --delay SECONDS        Delay between API calls (default: 0.5)
  --max N                Process at most N sessions
  --reindex              Re-process all sessions, even if already indexed
  --concurrency N        Parallel workers, 1-20 (default: 5)
```

Sessions are automatically filtered:
- **Minimum 3 turns** -- shorter sessions are skipped
- **Minimum 400 chars** of content -- trivial sessions are skipped
- **Already indexed** sessions are skipped (unless `--reindex`)

### Typical Workflows

```bash
# First-time: preview then index
eywa-batch --dry-run
eywa-batch

# Re-index everything (e.g., after schema change)
eywa-batch --reindex

# Process a small batch to test
eywa-batch --max 10

# Slow down for rate limits
eywa-batch --delay 2 --concurrency 2
```

## Troubleshooting

- **"No session JSONL files found"** -- Check `EYWA_SESSIONS_DIR` points to your Claude Code projects directory.
- **"OPENROUTER_API_KEY is not set"** -- Required for batch indexing. Set it in your environment or `.env` file.
- **Index seems stale** -- Run `eywa rebuild-index` to regenerate from stored handoff files.
- **Extraction fails** -- Ensure Node.js 18+ is installed and `npm install` was run in `eywa/extractors/`.
