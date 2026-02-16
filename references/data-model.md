# Eywa Data Model

How session memory is stored, indexed, and retrieved.

## Data Flow

```
Session JSONL --> extract / batch --> Handoff MD --> Index JSON --> get (retrieval)
     |                  |                  |              |              |
~/.claude/projects/   Node.js or       ~/.eywa/       ~/.eywa/      stdout
  (source)           OpenRouter       handoffs/    handoff-index.json
```

- **extract** uses the Claude Agent SDK (Node.js) at runtime -- one session at a time
- **batch** uses OpenRouter (any model) -- bulk processing of historical sessions

## Folder Structure

```
~/.eywa/                              # EYWA_DATA_DIR (configurable)
  handoff-index.json                  # Search index (one file, all sessions)
  handoffs/
    YYYY/
      MM/
        DD/
          <session_id>.md             # 8-char short ID from JSONL filename

~/.claude/projects/                   # EYWA_SESSIONS_DIR (configurable)
  <project-path>/
    <session-uuid>.jsonl              # Claude Code session files (read-only)
```

Session IDs are the first 8 characters of the JSONL filename UUID (e.g., `8735508d` from `8735508d-xxxx-xxxx-xxxx-xxxxxxxxxxxx.jsonl`).

## Handoff Document Format

Each handoff is a markdown file with YAML frontmatter and structured sections.

### Frontmatter Fields

```yaml
---
session_id: 8735508d               # 8-char short ID (from JSONL filename)
date: 2026-02-16                   # ISO date (YYYY-MM-DD)
duration: 10m                      # Human-readable duration string
model: claude-opus-4-6             # Model that ran the session
headline: Generated day plan       # Action-oriented, 5-10 words
projects: [pratchett-os, sorbent]  # Inferred from file paths
keywords: [day-plan, sorbent-commercial, coordinator-background-job]
substance: 1                       # 0=no work, 1=single task, 2=multi-step
---
```

### Document Sections

| Section | When present | Content |
|---------|-------------|---------|
| `# {headline}` | Always | Title matching frontmatter headline |
| `## What Happened` | substance >= 1 | 2-5 chronological bullets |
| `## Insights` | substance >= 1 | Decisions/learnings as `**Topic** -- explanation` |
| `## Key Files` | substance == 2 | Important files, one per line |
| `## Open Threads` | substance >= 1 | TODOs, unfinished work |

For `substance: 0` sessions: body is "No meaningful work." with no sections.

Full schema: `eywa/extractors/handoff_schema.json`

## Index Schema

`handoff-index.json` is a flat JSON file with four top-level keys:

```json
{
  "meta": {
    "last_updated": "2026-02-16T10:48:03.529150+00:00",
    "handoff_count": 489,
    "date_range": ["2026-01-08", "2026-02-16"]
  },
  "handoffs": {
    "<session_id>": {
      "date": "2026-02-16",
      "headline": "Generated day plan for 2026-02-16",
      "projects": ["pratchett-os", "sorbent"],
      "keywords": ["day-plan", "sorbent-commercial"],
      "substance": 1,
      "duration_minutes": 10
    }
  },
  "by_project": {
    "pratchett-os": ["8735508d", "1b2f6f6b"]
  },
  "by_keyword": {
    "day-plan": ["8735508d"],
    "sorbent-commercial": ["8735508d"]
  }
}
```

| Key | Purpose |
|-----|---------|
| `meta` | Index stats: last update timestamp, total count, date range |
| `handoffs` | Primary store: session_id to metadata (searchable fields only, not full text) |
| `by_project` | Inverted index: project name to list of session IDs |
| `by_keyword` | Inverted index: keyword to list of session IDs |

## Retrieval

`eywa get` uses keyword-based scoring with IDF weighting and recency decay:

- **Project matches** get 3x IDF weight (projects are high-signal)
- **Keyword matches** get 2x IDF weight (partial substring matching supported)
- **Recency boost** via `1 + 1/sqrt(age_days)` within the `--days-back` window
- **Decay** beyond `--days-back`: exponential half-life of 7 days
- Sessions with `substance: 0` are excluded from all results
- Without a query, returns the most recent sessions by date
