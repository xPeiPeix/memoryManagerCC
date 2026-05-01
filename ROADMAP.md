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

## V2 — Released (CLI ergonomics)

Shipped via PR #1 (`feat/v2-cli-polish`). All items reuse `MemoryStore` unchanged — the architecture invariant holds.

- **Positional `project` filter** — `mmcc list <project>` and `mmcc search <keyword> <project>` work without `--project`. Mutually exclusive with the flag.
- **Two-layer short id** — strips both the global prefix (`D--dev-code-`) and a category prefix (`AI-related-`, `Tools-`, `ICBC-`, …) when ≥ 2 projects share that category. Falls back to single-layer when stripping would create duplicates.
- **`--all-words` multi-word AND search** — splits on whitespace, requires every word to match somewhere across name + description + body.
- **`--fuzzy` typo-tolerant search** — `difflib.get_close_matches` with cutoff 0.7. Mutually exclusive with `--all-words`.
- **`mmcc add`** — create a new memory entry. Required: `--type` `--name`. Optional: `--description` `--project` (defaults to cwd) `--body` (else opens `$EDITOR`) `--origin-session-id`. Slug is derived from name; existing files raise `EntryExistsError` (exit 1).
- **`mmcc edit --name/--description/--type`** — patch frontmatter inline without opening the editor. Unknown frontmatter keys are preserved. Pass no flags to fall back to `$EDITOR`.

## V2.1 — Released (Notepad web viewer)

Shipped via PRs #2 / #3 / #4. Adds an experimental `mmcc notepad` subcommand — a local stdlib-only web SPA browsing all memory across projects, with type filter, live search, and one-click open in VSCode.

- **`mmcc notepad`** — pure stdlib HTTP server (`http.server` + `socketserver.ThreadingMixIn`) serving an embedded SPA. Three endpoints: `/` (HTML), `/api/projects` (project tree + entries), `/api/entry?path=...` (full content with path-traversal guard). marked.js loads from CDN.
- **`marked.use` raw-HTML strip (PR #3)** — neutralises the `html` token at the marked v12 renderer level so memory bodies cannot execute pasted `<script>` examples — a real UX hazard since users legitimately discuss XSS examples in feedback memories.
- **`connect_ex` port-conflict detection (PR #4)** — probes the candidate port with an active TCP handshake before binding. Catches Windows' default behaviour of allowing multiple sockets to bind the same LISTEN port (which would otherwise silently route requests to whichever process bound first).

`MemoryStore` reused unchanged — `notepad.py` is a thin HTTP wrapper. Architecture invariant holds.

## V3 — Next

Lightweight enhancements that still reuse `MemoryStore` unchanged.

- **MCP server** — expose `list_projects` / `list_entries` / `search` / `read_entry` / `add_entry` / `update_entry` as typed MCP tools so Claude can call from any IDE / Desktop, not only via Skill + Bash.

Trigger: real usage friction with V2, or feedback from external users.

## V4 — Long-term

Heavy features. Build only when V1 / V2 hit measurable limits.

- **SQLite FTS5 index** — drop-in via `IndexedMemoryStore(MemoryStore)` subclass when memory count > 1000 or search latency > 1s
- **Local embedding semantic search** — `远程主机` → `SSH server` cross-lingual semantic match
- **TUI** — Textual-based interactive browser (lazygit-style)
- **File watcher** — live refresh when Claude writes new memory in another session

Trigger: specific pain points. Do not pre-build.

---

**Architecture invariant across versions**: `MemoryStore` is a pure library. CLI, Skill, MCP, TUI are all thin wrappers over it.
