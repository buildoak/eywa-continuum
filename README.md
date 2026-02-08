# Eywa MCP

**Cross-session memory for Claude Code.**

[![Build](https://img.shields.io/badge/build-placeholder-lightgrey)](#)
[![PyPI](https://img.shields.io/badge/pypi-placeholder-lightgrey)](#)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## The Problem
Claude Code sessions are ephemeral. Context is lost between sessions, so you start fresh each time and re-explain what you were working on.

For heavy Claude Code users with hundreds of sessions, that context reset becomes a major productivity drain.

## The Solution
Eywa extracts a structured handoff at the end of each session and retrieves relevant past context at the start of the next one.

The name comes from the neural network in Avatar: Eywa connects sessions the way Eywa connects living memory.

## How It Works
Eywa runs a deterministic pipeline around your Claude Code transcripts:

1. Session Detection: 4-strategy fallback (explicit session ID, PID tracing, CWD mtime, global mtime).
2. Session Conversion: JSONL transcript -> normalized markdown conversation.
3. Extraction: LLM-powered structured handoff extraction.
4. Indexing: Inverted index with metadata + TF-IDF-friendly keyword/project maps.
5. Retrieval: Query keyword scoring + recency decay to return relevant handoffs.

```text
Claude Code JSONL Session
          |
          v
 [Session Detection]
          |
          v
 [JSONL -> Markdown]
          |
          v
 [Structured Extraction]
          |
          v
 [Handoff Markdown + Index]
          |
          v
      eywa_get()
```

## Two-Stage Setup
### Stage 1: Batch Index (one-time setup)
Use `eywa-batch` to process existing historical sessions in bulk through **OpenRouter**.

- You can pick any OpenRouter model (Gemini Flash, Claude, GPT, Llama, etc.)
- Default batch model: `google/gemini-3-flash-preview`

- Designed for hundreds of prior sessions
- Fast + low-cost extraction pass
- Builds your initial handoff corpus and index

### Stage 2: Runtime
Run `eywa-mcp` alongside Claude Code for ongoing sessions.

- Uses Claude (Sonnet) extraction at session end (`eywa_extract()`)
- Retrieves relevant context at session start (`eywa_get()`)

## Installation
### Prerequisites
- Python 3.10+
- Node.js 18+
- Claude Code

### 1) Install Python package
```bash
pip install -e .
```

### 2) Install Node extractor dependencies
```bash
cd eywa/extractors
npm install
cd ../..
```

### 3) Configure environment
```bash
cp .env.example .env
```

### 4) Register MCP server
Add to `claude_desktop_config.json` or `~/.claude.json`:

```json
{
  "mcpServers": {
    "eywa": {
      "command": "eywa-mcp"
    }
  }
}
```

### 5) Run manually (optional)
```bash
eywa-mcp
```

## Configuration
| Variable | Default | Description |
|---|---|---|
| `EYWA_DATA_DIR` | `~/.eywa` | Runtime storage root for handoffs and index |
| `EYWA_SESSIONS_DIR` | `~/.claude/projects` | Claude Code session JSONL root |
| `EYWA_TASKS_DIR` | `<EYWA_SESSIONS_DIR parent>/tasks` | Tasks directory used for PID-based session detection |
| `EYWA_CLAUDE_MODEL` | `sonnet` | Model used by runtime extraction (`eywa_extract`) |
| `EYWA_OPENROUTER_MODEL` | `google/gemini-3-flash-preview` | OpenRouter model used by batch indexing (`eywa-batch`) |
| `OPENROUTER_API_KEY` | _(unset)_ | OpenRouter API key for batch extraction |
| `EYWA_BATCH_DELAY` | `0.5` | Delay (seconds) between batch API calls |
| `EYWA_BATCH_CONCURRENCY` | `5` | Concurrent sessions processed by `eywa-batch` |
| `EYWA_TIMEZONE` | `UTC` | Timezone for rendered session timestamps |
| `EYWA_LOG_LEVEL` | `INFO` | Logging verbosity |

## Usage
Eywa exposes two MCP tools.

### `eywa_get()`
Retrieve relevant context from prior handoffs.

No query (recent sessions):
```json
{"max_handoffs": 3}
```

With query:
```json
{"query": "mcp tool routing and index scoring", "days_back": 30, "max_handoffs": 4}
```

With tighter options:
```json
{"query": "release pipeline", "days_back": 7, "max_handoffs": 2}
```

Sample output:

```markdown
## Eywa: 2 past sessions

# Implemented MCP routing fallback logic

## What Happened
- Added explicit tool dispatch guard for unknown tool names.
- Introduced parse-time validation for input payload constraints.

## Open Threads
- Add integration tests for malformed tool inputs.
```

### `eywa_extract()`
Extract and persist a handoff from the active session.

Auto-detect active session:
```json
{}
```

Explicit session ID:
```json
{"session_id": "12345678-1234-1234-1234-123456789abc"}
```

## Batch Indexing
Run one-time bulk import of historical sessions:

```bash
eywa-batch
```

Set your OpenRouter API key first:

```bash
export OPENROUTER_API_KEY=...
```

Choose a model (optional):

```bash
export EYWA_OPENROUTER_MODEL=anthropic/claude-3.5-sonnet
```

Dry run (no API calls):

```bash
eywa-batch --dry-run
```

Custom delay between calls:

```bash
eywa-batch --delay 1.0
```

Set concurrency (1-20):

```bash
eywa-batch --concurrency 10
```

Limit the run:

```bash
eywa-batch --max 50
```

Force reindex all sessions:

```bash
eywa-batch --reindex
```

What to expect:
- Scans `EYWA_SESSIONS_DIR` for `*.jsonl`
- Skips already-indexed sessions (unless `--reindex`)
- Skips very short/trivial sessions
- Uses OpenRouter Chat Completions with your selected model
- Writes handoffs to `YYYY/MM/DD/<session_id>.md`
- Updates `handoff-index.json` incrementally
- Prints progress and end-of-run summary

## License
MIT. See [LICENSE](LICENSE).
