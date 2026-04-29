---
name: memory-search
description: Browse and search Claude Code auto memory across all projects. Use this skill whenever the user asks to "find that memory I wrote in another project", "what feedback did I save about X", "search my memories for Y", "show me memories about Z", "list all my feedback notes", "which projects have memory about authentication / deployment / parsing / SSH / supabase / etc", "open that memory file from project A while I'm in project B", "我在某项目里写过一个关于 X 的 feedback", "搜一下我所有 memory 里提到 Y 的", or any cross-project memory recall question. Also trigger when the user mentions remembering writing something but cannot recall which project, or when they want a global view of their accumulated memory across the projects under ~/.claude/projects/. Match on the symptom — users usually just say "I wrote a memory about X somewhere" without mentioning the tool. Do NOT trigger for: writing new memories (that is automatic via Claude Code's own memory system), editing global ~/.claude/CLAUDE.md, or single-project memory inside the current session (Claude already has access).
allowed-tools: Bash(mmcc:*)
---

# memory-search: cross-project Claude Code memory browser

`mmcc` is a Python CLI that reads `~/.claude/projects/<encoded>/memory/*.md` directly (no index, no MCP, just file reads). Use it whenever the user asks about memory across projects.

## When to trigger

Match on any of these phrasings, even without the tool name:

- "I wrote a feedback about X but forgot which project"
- "search my memories for X"
- "what's that memory about migration / version bumping / SSH / supabase / ..."
- "list all feedback memories"
- "which projects have memory about Y"
- "open the X memory from the Y project"
- "show me all memories I've written this month"
- "我之前是不是在哪写过一个关于 ... 的笔记"
- "搜一下我所有 memory 里提到 ..."
- "把所有项目里的 feedback 列出来"

The user usually does not say "mmcc" or "memory-search" — recognize the symptom.

## Tool availability check

```bash
mmcc --help
```

If not installed, tell the user:

```bash
# pipx (recommended)
pipx install --editable D:/dev_code/AI_related/memoryManagerCC

# uv tool alternative
uv tool install --editable D:/dev_code/AI_related/memoryManagerCC
```

## Decision tree

```
User wants overview of all projects with memory?
  → mmcc tree

User wants list of memories (filter optional)?
  → mmcc list [--project X] [--type feedback]

User searching for a keyword?
  → mmcc search "<keyword>" [--type feedback]

User wants to read a specific memory?
  → mmcc cat <ref>          (full content)
  → mmcc which <ref>        (just the path, for piping)
  → mmcc edit <ref>         (open in $EDITOR)
```

## Standard workflow

1. Search across body + title + description (default = all open):
   ```bash
   mmcc search "<keyword>"
   ```
2. Narrow to feedback if user says "feedback / 陷阱 / 教训 / 坑":
   ```bash
   mmcc search "<keyword>" --type feedback
   ```
3. Output uses `<project_short>:<filename>` as the ref — copy that into `mmcc cat` to read full content.
4. Too many hits? Add `--type` or `--project <substring>` to narrow.

## Output format

Default plain text columnar output. Pass `--json` (anywhere — before or after the subcommand) when parsing programmatically:

```bash
mmcc list --json
mmcc --json search keyword
```

JSON shape: `{"items": [...], "errors": []}`.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Generic error (IO, parse) |
| 2 | NotFoundError — ref does not match any memory |
| 3 | AmbiguousRefError — multiple candidates; use `<project>:<filename>` form |

Use `if [ $? -eq 3 ]` in scripts to handle ambiguous refs separately.

## Edge cases

- **Worktree symlinks**: `mmcc tree` collapses worktree projects (whose `memory/` is a symlink to the main project) by default. Use `mmcc tree --all` to see them flagged with `→`.
- **Ambiguous ref**: if `mmcc cat foo.md` exits with code 3, copy one of the suggested `<project>:foo.md` candidates from stderr.
- **Missing memory dir**: many projects (especially `--claude-worktrees-` ones) have no memory at all. They will not appear in `mmcc tree` — this is expected.
- **Half-written file**: Claude may be writing memory concurrently in another session. mmcc silently skips files with malformed frontmatter. Re-run after Claude finishes if a result seems missing.
- **MEMORY.md is the index, not an entry**: `mmcc list` and `mmcc cat` ignore `MEMORY.md` because it is the per-project hand-written index.

## Encoding

Windows + Git Bash users: the installed `mmcc` entry point auto-reconfigures stdout/stderr to UTF-8, so Chinese filenames and content render correctly. No need to set `PYTHONIOENCODING` manually when invoking `mmcc`.

## Do NOT use this skill for

- Writing new memories — Claude Code does this automatically via its built-in memory tools
- Editing global `~/.claude/CLAUDE.md` — use the `Edit` tool directly
- Reading memories inside the current project's session — Claude already auto-loads them at session start
