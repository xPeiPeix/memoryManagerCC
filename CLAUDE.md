# mmcc — Claude instructions

Python CLI + Claude Code Skill for cross-project auto memory browsing/search. Reads `~/.claude/projects/<encoded>/memory/*.md` directly.

## Hard constraints

- **Pure stdlib only** — no runtime deps. PyYAML, click, rich, pydantic are forbidden. The frontmatter parser uses `re` by design.
- **`MemoryStore` is a pure library** — no argparse / IO / UI imports. CLI / Skill / notepad / future MCP all thin wrappers. V3 (MCP server, SQLite FTS5) must reuse `MemoryStore` unmodified.
- **Tests must use `tests/fixtures/projects/`**, never read real `~/.claude/projects/`.
- **Half-written files must not crash** — `parse_entry` returns `None` for any IO/parse failure; `list_entries` filters `None` out.
- **All writes are atomic** — use `_atomic_write(path, content)` (`tempfile.mkstemp + os.replace` on the same volume). Never `path.write_text(...)` directly for memory entries; a mid-write crash must not leave a half-written `.md` on disk.
- **`delete_entry` is restricted to memory entries** — `<project>/memory/*.md` shape only (`parent.name == "memory"` and `suffix == ".md"`). Calling on non-entry files (`sessions/*.jsonl`, project docs) raises `ValueError`. Do not relax this guard.
- **`update_entry(body=...)` requires str or None** — non-string body raises `ValueError` so the HTTP layer can return 400 instead of 500.
- **UTF-8 always**: Use `read_text(encoding="utf-8")`. CLI reconfigures stdout/stderr to utf-8 in `_ensure_utf8()`.

## File responsibilities

| File | Purpose |
|------|---------|
| `src/mmcc/paths.py` | Encoded project dir decoding + cross-platform normalize. 4 prefix patterns: `D--`, `d--`, `C--Users-`, `-mnt-d-` |
| `src/mmcc/store.py` | `MemoryEntry` / `ProjectInfo` dataclass + `MemoryStore` class (the pure library) + `_atomic_write` helper |
| `src/mmcc/notepad.py` | stdlib HTTP server + embedded SPA. GET `/`, GET `/api/projects`, GET/PUT/DELETE `/api/entry`. `connect_ex` port detection. `_safe_resolve` for path-traversal guard. `_INDEX_HTML` is a single raw string holding CSS/JS — keep `marked.use(renderer)` raw-HTML strip intact (XSS guard) |
| `src/mmcc/render.py` | Plain text + JSON formatters. No business logic |
| `src/mmcc/cli.py` | argparse subcommand dispatch. Exit codes 0/1/2/3 |
| `skills/memory-search/SKILL.md` | Symptom-matching description in `claude-repath` style |

## Worktree alias detection

Git Bash creates MSYS2 symlinks that Python's `is_symlink()` does not recognize. `paths.is_worktree_alias` falls back to comparing `md.resolve().parent` vs `project_dir.resolve()` — if they differ, it's an alias.

## ref format

`<project_short>:<filename>`. `project_short` = project_id with the majority-prefix family's longest common prefix stripped. The LCP calculation excludes IDs that are pure prefixes of other IDs (e.g. `D--dev-code` is excluded so `D--dev-code-` becomes the prefix).
