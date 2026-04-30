from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import __version__, render
from .paths import PROJECTS_DIR
from .store import (
    AmbiguousRefError,
    EntryExistsError,
    MemoryStore,
    NotFoundError,
)


def _ensure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        enc = getattr(stream, "encoding", None)
        if enc and enc.lower() != "utf-8" and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="JSON output")
    common.add_argument("--projects-dir", type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    p = argparse.ArgumentParser(
        prog="mmcc",
        description="Browse and search Claude Code auto memory across projects",
        parents=[common],
    )
    p.add_argument("--version", action="version", version=f"mmcc {__version__}")

    sub = p.add_subparsers(dest="command", required=True)

    tree = sub.add_parser("tree", help="List projects with memory + counts", parents=[common])
    tree.add_argument("--all", action="store_true", help="Include worktree alias + empty projects")

    lst = sub.add_parser("list", help="List memory entries", parents=[common])
    lst_g = lst.add_mutually_exclusive_group()
    lst_g.add_argument("project_pos", nargs="?", default=None, metavar="PROJECT",
                       help="Filter by project (id/short id/substring)")
    lst_g.add_argument("--project", dest="project", default=None,
                       help="Filter by project (id/short id/substring)")
    lst.add_argument("--type", choices=["feedback", "user", "project", "reference", "unknown"])
    lst.add_argument("--limit", type=int, default=50, help="0 = no limit")

    s = sub.add_parser("search", help="Search across memory", parents=[common])
    s.add_argument("keyword")
    s_g = s.add_mutually_exclusive_group()
    s_g.add_argument("project_pos", nargs="?", default=None, metavar="PROJECT",
                     help="Filter by project (id/short id/substring)")
    s_g.add_argument("--project", dest="project", default=None,
                     help="Filter by project (id/short id/substring)")
    s.add_argument("--body", action="store_true", help="Search body only (default: all)")
    s.add_argument("--title", action="store_true", help="Search title only (default: all)")
    s.add_argument("--description", action="store_true", help="Search description only (default: all)")
    s.add_argument("--type", choices=["feedback", "user", "project", "reference", "unknown"])
    s.add_argument("--case-sensitive", action="store_true")
    s_mode = s.add_mutually_exclusive_group()
    s_mode.add_argument("--all-words", action="store_true",
                        help="Match all whitespace-separated words (AND semantics)")
    s_mode.add_argument("--fuzzy", action="store_true",
                        help="Fuzzy match via difflib (typo tolerance, cutoff 0.7)")
    s.add_argument("--limit", type=int, default=50)

    cat = sub.add_parser("cat", help="Show memory full content", parents=[common])
    cat.add_argument("ref")
    cat.add_argument("--no-frontmatter", action="store_true")

    edit = sub.add_parser("edit", help="Open memory in $EDITOR or patch frontmatter", parents=[common])
    edit.add_argument("ref")
    edit.add_argument("--name", default=None,
                      help="Patch frontmatter name (skips editor)")
    edit.add_argument("--description", default=None,
                      help="Patch frontmatter description (skips editor)")
    edit.add_argument("--type", default=None,
                      choices=["feedback", "user", "project", "reference"],
                      help="Patch frontmatter type (skips editor)")

    which = sub.add_parser("which", help="Print memory file path", parents=[common])
    which.add_argument("ref")
    which.add_argument("--windows-style", action="store_true",
                       help="Use backslash paths (default: forward slash)")

    add = sub.add_parser("add", help="Create a new memory entry", parents=[common])
    add.add_argument("--type", required=True,
                     choices=["feedback", "user", "project", "reference"])
    add.add_argument("--name", required=True)
    add.add_argument("--description", default="")
    add.add_argument("--project", default=None,
                     help="Target project (id/short id/substring); defaults to cwd")
    add.add_argument("--body", default=None,
                     help="Body text inline; if omitted, opens $EDITOR")
    add.add_argument("--origin-session-id", dest="origin_session_id", default=None)

    return p


def _make_store(args: argparse.Namespace) -> MemoryStore:
    pd = getattr(args, "projects_dir", None)
    return MemoryStore(pd if pd else PROJECTS_DIR)


def _cmd_tree(args: argparse.Namespace, store: MemoryStore) -> int:
    projects = store.list_projects(include_aliases=args.all, include_empty=args.all)
    print(render.fmt_tree(projects, store.short_id, json_mode=args.json))
    return 0


def _cmd_list(args: argparse.Namespace, store: MemoryStore) -> int:
    project = args.project_pos or args.project
    entries = store.list_entries(project_id=project, type_filter=args.type)
    if args.limit > 0:
        entries = entries[: args.limit]
    print(render.fmt_list(entries, store.short_id, json_mode=args.json))
    return 0


def _cmd_search(args: argparse.Namespace, store: MemoryStore) -> int:
    explicit = args.body or args.title or args.description
    if explicit:
        in_body = args.body
        in_title = args.title
        in_description = args.description
    else:
        in_body = in_title = in_description = True
    project = args.project_pos or args.project
    results = store.search(
        args.keyword,
        in_body=in_body,
        in_title=in_title,
        in_description=in_description,
        type_filter=args.type,
        project_id=project,
        case_sensitive=args.case_sensitive,
        all_words=args.all_words,
        fuzzy=args.fuzzy,
    )
    if args.limit > 0:
        results = results[: args.limit]
    print(render.fmt_search(results, store.short_id, json_mode=args.json))
    return 0


def _resolve_or_exit(store: MemoryStore, ref: str) -> tuple[Optional[object], int]:
    try:
        return store.find(ref), 0
    except NotFoundError as e:
        print(str(e), file=sys.stderr)
        return None, 2
    except AmbiguousRefError as e:
        print(render.fmt_ambiguous(e, store.short_id), file=sys.stderr)
        return None, 3


def _cmd_cat(args: argparse.Namespace, store: MemoryStore) -> int:
    entry, code = _resolve_or_exit(store, args.ref)
    if entry is None:
        return code
    print(render.fmt_cat(entry, no_frontmatter=args.no_frontmatter, json_mode=args.json))
    return 0


def _cmd_edit(args: argparse.Namespace, store: MemoryStore) -> int:
    entry, code = _resolve_or_exit(store, args.ref)
    if entry is None:
        return code
    has_flag = args.name is not None or args.description is not None or args.type is not None
    if has_flag:
        try:
            updated = store.update_entry(
                entry.file_path,
                name=args.name,
                description=args.description,
                type=args.type,
            )
        except (ValueError, OSError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        print(f"Updated: {updated.file_path}")
        return 0
    editor = _find_editor()
    if not editor:
        print("No editor found. Set $EDITOR or $VISUAL.", file=sys.stderr)
        return 1
    try:
        subprocess.Popen([editor, str(entry.file_path)])
    except (OSError, FileNotFoundError) as e:
        print(f"Failed to launch {editor}: {e}", file=sys.stderr)
        return 1
    return 0


def _cmd_which(args: argparse.Namespace, store: MemoryStore) -> int:
    entry, code = _resolve_or_exit(store, args.ref)
    if entry is None:
        return code
    print(render.fmt_which(entry, windows_style=args.windows_style))
    return 0


def _detect_project_id_from_cwd() -> Optional[str]:
    s = str(Path.cwd()).replace("\\", "/")
    if len(s) >= 3 and s[1] == ":":
        drive = s[0]
        rest = s[3:]
        encoded = f"{drive}--{rest.replace('/', '-')}"
        return encoded.rstrip("-") or None
    if s.startswith("/mnt/") and len(s) >= 7:
        return ("-" + s[1:].replace("/", "-")).rstrip("-") or None
    if s.startswith("/"):
        return ("-" + s[1:].replace("/", "-")).rstrip("-") or None
    return None


def _find_editor() -> Optional[str]:
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if editor:
        return editor
    for cmd in ("code", "notepad"):
        if shutil.which(cmd):
            return cmd
    return None


def _prompt_body_via_editor() -> Optional[str]:
    editor = _find_editor()
    if not editor:
        print("No editor found. Set $EDITOR/$VISUAL or pass --body inline.", file=sys.stderr)
        return None
    import shlex
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False, encoding="utf-8") as f:
        f.write("# Type body below; lines starting with # will be stripped.\n")
        tmp = f.name
    try:
        try:
            parts = shlex.split(editor)
        except ValueError:
            parts = [editor]
        if not parts:
            parts = [editor]
        try:
            subprocess.run([*parts, tmp], check=False)
        except (OSError, FileNotFoundError) as e:
            print(f"Failed to launch {editor!r}: {e}", file=sys.stderr)
            return None
        try:
            with open(tmp, "r", encoding="utf-8") as fh:
                raw = fh.read()
        except OSError as e:
            print(f"Failed to read editor output: {e}", file=sys.stderr)
            return None
        lines = [ln for ln in raw.splitlines() if not ln.startswith("#")]
        body = "\n".join(lines).strip()
        return body if body else None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _cmd_add(args: argparse.Namespace, store: MemoryStore) -> int:
    project_id = args.project
    if not project_id:
        project_id = _detect_project_id_from_cwd()
        if not project_id:
            print("Error: --project required (could not detect from cwd).", file=sys.stderr)
            return 1
    body = args.body
    if body is None:
        body = _prompt_body_via_editor()
        if body is None:
            print("Error: empty body, aborting.", file=sys.stderr)
            return 1
    try:
        path = store.add_entry(
            project_id=project_id,
            name=args.name,
            type=args.type,
            description=args.description,
            body=body,
            origin_session_id=args.origin_session_id,
        )
    except (EntryExistsError, NotFoundError, ValueError, OSError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Created: {path}")
    return 0


_HANDLERS = {
    "tree": _cmd_tree,
    "list": _cmd_list,
    "search": _cmd_search,
    "cat": _cmd_cat,
    "edit": _cmd_edit,
    "which": _cmd_which,
    "add": _cmd_add,
}


def main(argv: Optional[list[str]] = None) -> int:
    _ensure_utf8()
    if argv is None:
        argv = sys.argv[1:]
    has_json_anywhere = "--json" in argv
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.json = args.json or has_json_anywhere
    store = _make_store(args)
    handler = _HANDLERS.get(args.command)
    if handler is None:
        parser.print_help()
        return 1
    try:
        return handler(args, store)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
