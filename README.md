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

# List memory entries
mmcc list
mmcc list --type feedback
mmcc list --project quant       # Substring match on project name

# Search across all projects
mmcc search "version"
mmcc search "auth" --type feedback
mmcc search "redis" --project gateway --case-sensitive

# Inspect a single memory (ref = <project_short>:<filename>)
mmcc cat AI-related-claude-repath:feedback_version_bump_checklist.md
mmcc cat feedback_version_bump_checklist.md      # Bare filename if unique
mmcc which <ref>                                  # Print absolute path
mmcc edit <ref>                                   # Open in $EDITOR / VISUAL
```

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
