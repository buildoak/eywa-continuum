---
name: eywa
description: >
  Cross-session memory and continuity CLI. Provides session memory, handoff documents,
  and historical session retrieval. Use at session start to retrieve context from past
  sessions (eywa get). Use at session end to persist a handoff document for future
  sessions (eywa extract). Trigger on: "session memory", "handoff", "continuity",
  "past sessions", "what was I working on", "session start", "session end",
  "batch index", "historical sessions", "prior context", "previous session".
---

# Eywa CLI

Cross-session memory. Extracts handoffs at session end, retrieves past context at session start.

## Execution

**All commands use this exact pattern — no variations:**

```bash
cd /Users/otonashi/thinking/pratchett-os/coordinator/.claude/skills/eywa-continuum && /Users/otonashi/thinking/pratchett-os/.venv/bin/python -m eywa.cli <command> [args]
```

### Copy-paste commands

```bash
# Get recent sessions (default 3)
cd /Users/otonashi/thinking/pratchett-os/coordinator/.claude/skills/eywa-continuum && /Users/otonashi/thinking/pratchett-os/.venv/bin/python -m eywa.cli get

# Get by keyword search
cd /Users/otonashi/thinking/pratchett-os/coordinator/.claude/skills/eywa-continuum && /Users/otonashi/thinking/pratchett-os/.venv/bin/python -m eywa.cli get "mcp routing"

# Get with options
cd /Users/otonashi/thinking/pratchett-os/coordinator/.claude/skills/eywa-continuum && /Users/otonashi/thinking/pratchett-os/.venv/bin/python -m eywa.cli get "topic" --days-back 30 --max 5

# Extract current session (auto-detect)
cd /Users/otonashi/thinking/pratchett-os/coordinator/.claude/skills/eywa-continuum && /Users/otonashi/thinking/pratchett-os/.venv/bin/python -m eywa.cli extract

# Extract specific session (8-char short ID or full UUID)
cd /Users/otonashi/thinking/pratchett-os/coordinator/.claude/skills/eywa-continuum && /Users/otonashi/thinking/pratchett-os/.venv/bin/python -m eywa.cli extract 1b2f6f6b

# Rebuild index
cd /Users/otonashi/thinking/pratchett-os/coordinator/.claude/skills/eywa-continuum && /Users/otonashi/thinking/pratchett-os/.venv/bin/python -m eywa.cli rebuild-index
```

Output goes to stdout, errors to stderr. Exit code 0 = success, 1 = failure.

**Why `cd` + `python -m`:** The `eywa` package lives in the skill directory, not on PATH. The venv Python picks it up from CWD. Do not try bare `eywa` — it does not exist as a binary.

## When to Call

**eywa get:** Session start (no query), when user asks about past work (keyword query), when you need prior decisions.

**eywa extract:** Session end, after significant milestones, when user asks to save context.

**Extract is a two-step protocol:**
1. **Summary first.** Before running `eywa extract`, produce a concise session summary inline — what was built, what decisions were made, what's open. This is your own synthesis, not a tool call. Write it as a message to the user.
2. **Then extract.** Run `eywa extract` (or `eywa extract <session_id>`). The CLI independently parses the JSONL transcript. Your summary above becomes part of the transcript, enriching the handoff with an explicit recap.

**Summary format depends on channel:**

*Claude Code PC session (default):*
- A **table** of items built/changed (columns: item, status, notes)
- Followed by 2-3 succinct paragraphs: decisions made, patterns discovered, open threads

*Telegram session:*
- Short **bulleted list** of items done
- Short **bulleted list** of pending/open items
- 2-3 succinct paragraphs covering decisions and context

**eywa rebuild-index:** After manually editing handoff files, corrupt index, after bulk imports.

## Session Detection (extract)

Without `session_id`: auto-detects via PID tracing → CWD-scoped mtime → global mtime. Requires JSONL modified within 30 seconds.

With `session_id`: explicit lookup only (8-char short ID or full UUID).

In multi-session environments, pass session_id explicitly.

## Bundled Resources

| Path | What | When to load |
|------|------|-------------|
| `references/data-model.md` | Handoff format, folder structure, index schema | When you need to understand where data lives or debug retrieval |
| `references/setup-guide.md` | First-time setup, model config, env vars, batch indexing | When setting up eywa for the first time or configuring models |
| `eywa/cli.py` | CLI entry point | When debugging CLI behavior |
| `eywa/extractors/handoff_schema.json` | Handoff document JSON schema | When validating handoff output |
| `setup.sh` | Idempotent setup script | First-time installation |

## Tips

- **Keywords, not sentences.** "mcp server" not "let's continue working on the MCP server"
- **Don't extract trivial sessions.** Empty work = empty handoffs.
- **max > 5 is capped.** 3 is usually enough — more dilutes context.
- **Check exit codes.** Exit 1 means failure.

## Anti-Patterns

- **Do not run `eywa extract` on trivial sessions.** Sessions under 3 turns or 400 chars of content produce low-value handoffs.
- **Do not skip `eywa-batch` on first install.** Without historical context, `eywa get` returns nothing useful.
- **Do not use `eywa get` without a query when many handoffs exist.** It returns recent sessions by default — use keywords for targeted retrieval.
- **Do not hardcode paths.** Use env vars (`EYWA_DATA_DIR`, `EYWA_SESSIONS_DIR`) for portability.
