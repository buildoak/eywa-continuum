---
name: eywa
description: >
  Cross-session memory CLI. Use at session start to retrieve context from past sessions
  (eywa get). Use at session end to persist a handoff document for future sessions
  (eywa extract). Use when the user references past work, asks "what was I working on",
  or when you need continuity from a prior session.
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

**eywa rebuild-index:** After manually editing handoff files, corrupt index, after bulk imports.

## Session Detection (extract)

Without `session_id`: auto-detects via PID tracing → CWD-scoped mtime → global mtime. Requires JSONL modified within 30 seconds.

With `session_id`: explicit lookup only (8-char short ID or full UUID).

In multi-session environments, pass session_id explicitly.

## Tips

- **Keywords, not sentences.** "mcp server" not "let's continue working on the MCP server"
- **Don't extract trivial sessions.** Empty work = empty handoffs.
- **max > 5 is capped.** 3 is usually enough — more dilutes context.
- **Check exit codes.** Exit 1 means failure.
