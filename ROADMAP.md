# Roadmap

## V1 — Released

- Pure stdlib CLI (zero runtime deps)
- 6 subcommands: `tree` / `list` / `search` / `cat` / `edit` / `which`
- Cross-project auto memory discovery — reads `~/.claude/projects/*/memory/*.md` directly
- Cross-platform path normalization (4 encoded prefixes + Git Bash + worktree symlink fallback)
- CJK display-width alignment
- Claude Code Skill (`memory-search`) for symptom-based auto-trigger
- Exit codes 0/1/2/3 for script integration
- 63 tests passing on Linux + Windows × Python 3.11/3.12/3.13

## V2 — Next

Lightweight enhancements that reuse `MemoryStore` unchanged.

- **MCP server** — expose `list_projects` / `list_entries` / `search` / `read_entry` as typed MCP tools so Claude can call from any IDE / Desktop, not only via Skill + Bash
- **`--all-words` multi-word AND search** — match memories containing every listed term
- **`--fuzzy` typo-tolerant search** — stdlib `difflib` close-match fallback
- **`mmcc add` / extended `mmcc edit`** — write new memory or update frontmatter from CLI

Trigger: real usage friction with V1, or feedback from external users.

## V3 — Long-term

Heavy features. Build only when V1 / V2 hit measurable limits.

- **SQLite FTS5 index** — drop-in via `IndexedMemoryStore(MemoryStore)` subclass when memory count > 1000 or search latency > 1s
- **Local embedding semantic search** — `远程主机` → `SSH server` cross-lingual semantic match
- **TUI** — Textual-based interactive browser (lazygit-style)
- **File watcher** — live refresh when Claude writes new memory in another session

Trigger: specific pain points. Do not pre-build.

---

**Architecture invariant across versions**: `MemoryStore` is a pure library. CLI, Skill, MCP, TUI are all thin wrappers over it.
