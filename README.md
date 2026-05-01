# mmcc — Cross-project Claude Code auto memory browser

A tiny Python CLI + Claude Code Skill that lets you browse and search Claude Code's [auto memory](https://code.claude.com/docs/en/memory) across all your projects.

## Why

Claude Code v2.1.59+ stores per-project auto memory under `~/.claude/projects/<encoded>/memory/*.md`. The design is intentionally project-isolated, but in practice you accumulate memory across many projects and need it globally:

- "I wrote a feedback about version bumping somewhere — which project?"
- "Search all my memories for `subprocess.run`"
- "List every feedback note across all projects"

`mmcc` reads those `.md` files directly. **No index, no MCP server, no daemon — just file reads.**

## Demo

```
$ mmcc tree
PROJECT                           COUNT  INDEX?  MTIME
AI-related-ai-gateways                2     yes  2026-04-28 22:43
quantitative-trading                 37     yes  2026-04-28 09:17
AI-related-claude-repath              2     yes  2026-04-24 16:53
Life-time-blocks                      8     yes  2026-04-24 12:22
...

$ mmcc search "version" --limit 1
[feedback] AI-related-claude-repath:feedback_version_bump_checklist.md  claude-repath 版本号升级完整 checklist
    每次给 claude-repath 项目升版本号（bump version / prepare release），必须按以下清单逐项过一遍...
    1. `pyproject.toml` — `version = "X.Y.Z"`（PyPI 发布源）
    2. `src/claude_repath/__init__.py` — `__version__ = "X.Y.Z"`（CLI `--version` 输出源）

$ mmcc cat AI-related-claude-repath:feedback_version_bump_checklist.md
```

The bundled Claude Code Skill triggers `mmcc search` automatically when you ask things like *"我之前在哪写过版本号升级的 feedback"*.

## How it differs from neighbors

| Tool | Reads Claude's auto memory | Cross-project | Index needed | Notes |
|------|:--:|:--:|:--:|------|
| **mmcc** | ✅ | ✅ | ❌ stdlib-only | Direct file reads, ~80ms for 80+ files |
| [CCO](https://github.com/mcpware/claude-code-organizer) | ✅ (recognises) | ❌ per-project sidebar | ❌ | General config dashboard, no aggregation |
| [claude-mem](https://github.com/thedotmack/claude-mem) | ❌ writes its own | ✅ | Claude SDK | Auto-captures sessions, not auto memory |
| [memsearch](https://github.com/zc277584121/memsearch) | ❌ separate vault | ✅ | Milvus + embeddings | Multi-agent vault, heavier |
| [cccmemory](https://github.com/xiaolai/claude-conversation-memory-mcp) | ❌ session JSONL | ✅ | embeddings | Conversation history, not memory files |

## Install

```bash
# Recommended (Python 3.11+)
pipx install --editable .

# Or with uv
uv tool install --editable .
```

Then install the optional Claude Code skill so the assistant can call `mmcc` for you:

```bash
ln -s "$(pwd)/skills/memory-search" ~/.claude/skills/memory-search
# Windows without Developer Mode:
cp -r skills/memory-search ~/.claude/skills/
```

## Usage

```bash
# Project tree with memory counts
mmcc tree
mmcc tree --all                 # Include worktree alias projects

# List memory entries (project filter accepts a positional or --project)
mmcc list
mmcc list quant                 # Positional substring match
mmcc list --project quant       # Equivalent --project form
mmcc list --type feedback

# Search across all projects (keyword positional, optional project positional)
mmcc search "version"
mmcc search "auth" --type feedback
mmcc search "redis" gateway --case-sensitive          # Positional project filter
mmcc search "supabase auth" --all-words               # AND across whitespace-split words
mmcc search "athentication" --fuzzy                   # difflib typo tolerance (cutoff 0.7)

# Inspect a single memory (ref = <project_short>:<filename>)
mmcc cat claude-repath:feedback_version_bump_checklist.md
mmcc cat feedback_version_bump_checklist.md           # Bare filename if unique
mmcc which <ref>                                      # Print absolute path

# Edit: open in editor by default, or patch frontmatter inline (skips editor)
mmcc edit <ref>
mmcc edit <ref> --description "new description"
mmcc edit <ref> --name "new name" --type feedback

# Add a new memory entry (writes ~/.claude/projects/<id>/memory/<type>_<slug>.md)
mmcc add --type feedback --name "lesson learned" \
         --description "short summary" --body "full body text" \
         --project mmcc                                # --project defaults to cwd
mmcc add --type project --name "context note" \
         --description "background" --project gateway   # Omit --body to open $EDITOR

# Launch local web viewer + CRUD editor for all memory
mmcc notepad                                            # Auto-pick port from 8765
mmcc notepad --port 8080                                # Use a specific port
mmcc notepad --no-browser                               # Don't auto-open browser
```

`mmcc tree` shows project short ids with both the global prefix (e.g. `D--dev-code-`) and a category prefix (e.g. `AI-related-`, `Tools-`) stripped when at least two projects share that category — so `D--dev-code-AI-related-CCometixLine` displays as `CCometixLine`. The full project id is still accepted by every filter.

`mmcc search` flags `--all-words` and `--fuzzy` are mutually exclusive. Default search is a single literal substring across name + description + body; pass `--body` / `--title` / `--description` to scope further.

`mmcc add` requires `--type` and `--name`. The body comes from `--body` if given, otherwise `$EDITOR` / `$VISUAL` (`code` / `notepad` as fallback) opens a temporary file. The slug for the filename is derived from `--name` (lowercase, spaces and hyphens to underscores, non-word chars stripped). Existing files are never overwritten — `mmcc add` exits with code 1 and `EntryExistsError`.

`mmcc edit <ref> --name/--description/--type` patches frontmatter in place without opening the editor; unknown frontmatter keys are preserved. Pass no flags to fall back to `$EDITOR`.

`mmcc notepad` opens a local SPA at `http://localhost:8765` (or the next free port from 8766) showing all memory across projects with a type filter and live search. Pure stdlib HTTP server, marked.js loads from CDN. Cross-platform port-conflict detection via `connect_ex` probe — auto-switches to the next free port when 8765 is busy, or raises a clear error if `--port <N>` is explicitly occupied. Memory bodies render as markdown with raw HTML stripped (so any `<script>` inside a body shows as text, never executes).

The SPA uses an **Inter + JetBrains Mono** light theme with a blue accent (`#2563eb`) and per-type color pills (feedback amber, user green, project blue, reference purple). Top bar: collapsible sidebar toggle, search box with `Ctrl/Cmd+K` shortcut, and a row of type-filter tabs (`all` / `feedback` / `user` / `project` / `reference`). The viewer pane renders a `<project> / memory / <filename>` breadcrumb, the type pill, title, description, and a Notion-style toolbar (`编辑` · `复制路径` · `在 VSCode 打开` · `删除`). **CRUD inline**: edit mode lets you patch name / description / body (`Ctrl/Cmd+S` to save, `Esc` to cancel); delete shows an in-page confirm card before removing the file. Deletion is restricted to memory entries (`<project>/memory/*.md`) — non-entry files under the projects tree (e.g. `sessions/*.jsonl`) cannot be deleted via the API. Writes use atomic `tempfile + os.replace` so a mid-write crash never leaves a half-written file on disk.

## Output formats

Default: plain columnar text. Add `--json` (before or after the subcommand) for machine-parseable output:

```bash
mmcc list --json | jq '.items[] | {ref, name}'
mmcc --json search keyword
```

JSON shape: `{"items": [...], "errors": []}`.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Generic error |
| 2 | Ref not found |
| 3 | Ref ambiguous — use `<project>:<filename>` form |

## Tests

```bash
python -m pytest
```

Tests use `tests/fixtures/projects/` mock data — they never touch your real `~/.claude/projects/`.

## License

MIT
