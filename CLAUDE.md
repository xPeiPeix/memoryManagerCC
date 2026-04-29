# mmcc — Claude instructions

Python CLI + Claude Code Skill for cross-project auto memory browsing/search. Reads `~/.claude/projects/<encoded>/memory/*.md` directly.

## Hard constraints

- **Pure stdlib only** — no runtime deps. PyYAML, click, rich, pydantic are forbidden. The frontmatter parser uses `re` by design.
- **`MemoryStore` is a pure library** — no argparse / IO / UI imports. CLI and Skill are thin wrappers. V2 (MCP server, SQLite FTS5) must reuse `MemoryStore` unmodified.
- **Tests must use `tests/fixtures/projects/`**, never read real `~/.claude/projects/`.
- **Half-written files must not crash** — `parse_entry` returns `None` for any IO/parse failure; `list_entries` filters `None` out.
- **UTF-8 always**: Use `read_text(encoding="utf-8")`. CLI reconfigures stdout/stderr to utf-8 in `_ensure_utf8()`.

## File responsibilities

| File | Purpose |
|------|---------|
| `src/mmcc/paths.py` | Encoded project dir decoding + cross-platform normalize. 4 prefix patterns: `D--`, `d--`, `C--Users-`, `-mnt-d-` |
| `src/mmcc/store.py` | `MemoryEntry` / `ProjectInfo` dataclass + `MemoryStore` class (the pure library) |
| `src/mmcc/render.py` | Plain text + JSON formatters. No business logic |
| `src/mmcc/cli.py` | argparse subcommand dispatch. Exit codes 0/1/2/3 |
| `skills/memory-search/SKILL.md` | Symptom-matching description in `claude-repath` style |

## Worktree alias detection

Git Bash creates MSYS2 symlinks that Python's `is_symlink()` does not recognize. `paths.is_worktree_alias` falls back to comparing `md.resolve().parent` vs `project_dir.resolve()` — if they differ, it's an alias.

## ref format

`<project_short>:<filename>`. `project_short` = project_id with the majority-prefix family's longest common prefix stripped. The LCP calculation excludes IDs that are pure prefixes of other IDs (e.g. `D--dev-code` is excluded so `D--dev-code-` becomes the prefix).
