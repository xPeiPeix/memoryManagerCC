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
    s.add_argument("--limit", type=int, default=50)

    cat = sub.add_parser("cat", help="Show memory full content", parents=[common])
    cat.add_argument("ref")
    cat.add_argument("--no-frontmatter", action="store_true")

    edit = sub.add_parser("edit", help="Open memory in $EDITOR", parents=[common])
    edit.add_argument("ref")

    which = sub.add_parser("which", help="Print memory file path", parents=[common])
    which.add_argument("ref")
    which.add_argument("--windows-style", action="store_true",
                       help="Use backslash paths (default: forward slash)")

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
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not editor:
        for cmd in ("code", "notepad"):
            if shutil.which(cmd):
                editor = cmd
                break
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


_HANDLERS = {
    "tree": _cmd_tree,
    "list": _cmd_list,
    "search": _cmd_search,
    "cat": _cmd_cat,
    "edit": _cmd_edit,
    "which": _cmd_which,
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
