from __future__ import annotations

import json
import shutil
from datetime import datetime
from typing import Callable

from .store import (
    AmbiguousRefError,
    MemoryEntry,
    ProjectInfo,
)

ShortIdFn = Callable[[str], str]


def _fmt_mtime(mtime: float) -> str:
    if not mtime:
        return ""
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")


def _is_wide(c: str) -> bool:
    o = ord(c)
    if o < 0x1100:
        return False
    return (
        0x1100 <= o <= 0x115F
        or 0x2E80 <= o <= 0x303E
        or 0x3041 <= o <= 0x33FF
        or 0x3400 <= o <= 0x4DBF
        or 0x4E00 <= o <= 0x9FFF
        or 0xA000 <= o <= 0xA4CF
        or 0xAC00 <= o <= 0xD7A3
        or 0xF900 <= o <= 0xFAFF
        or 0xFE30 <= o <= 0xFE4F
        or 0xFF00 <= o <= 0xFF60
        or 0xFFE0 <= o <= 0xFFE6
    )


def _display_width(s: str) -> int:
    return sum(2 if _is_wide(c) else 1 for c in s)


def _truncate(s: str, width: int) -> str:
    if _display_width(s) <= width:
        return s
    cur = 0
    out = []
    for c in s:
        w = 2 if _is_wide(c) else 1
        if cur + w + 1 > width:
            break
        out.append(c)
        cur += w
    return "".join(out) + "…"


def _pad(s: str, width: int, align: str = "<") -> str:
    cur = _display_width(s)
    if cur >= width:
        return s
    pad = " " * (width - cur)
    if align == ">":
        return pad + s
    return s + pad


def _terminal_width(default: int = 140) -> int:
    return shutil.get_terminal_size((default, 24)).columns


def _compute_widths(refs: list[str], names: list[str], term_width: int,
                    type_w: int = 10, mtime_w: int = 16) -> tuple[int, int]:
    max_ref = max((_display_width(r) for r in refs), default=_display_width("REF"))
    max_name = max((_display_width(n) for n in names), default=_display_width("NAME"))
    sep = 6
    available = term_width - type_w - mtime_w - sep
    if max_ref + max(20, max_name) <= available:
        ref_w = max_ref
        name_w = min(max_name, available - ref_w)
    elif max_ref + 20 <= available:
        ref_w = max_ref
        name_w = available - ref_w
    else:
        name_w = 20
        ref_w = max(_display_width("REF"), available - name_w)
    return ref_w, name_w


def fmt_tree(projects: list[ProjectInfo], short_id_fn: ShortIdFn, *, json_mode: bool = False) -> str:
    if json_mode:
        items = [{
            "project_id": p.project_id,
            "project_short": short_id_fn(p.project_id),
            "project_path": p.project_path,
            "memory_dir": str(p.memory_dir) if p.memory_dir else None,
            "is_alias": p.is_alias,
            "entry_count": p.entry_count,
            "has_index": p.has_index,
            "mtime": p.mtime,
        } for p in projects]
        return json.dumps({"items": items, "errors": []}, ensure_ascii=False, indent=2)
    if not projects:
        return "(no projects with memory found)"
    sids = []
    for p in projects:
        sid = short_id_fn(p.project_id)
        if p.is_alias:
            sid = "→ " + sid
        sids.append(sid)
    proj_w = max(max((_display_width(s) for s in sids), default=0), _display_width("PROJECT"))
    lines = [
        _pad("PROJECT", proj_w) + "  " + _pad("COUNT", 5, ">") + "  " + _pad("INDEX?", 6, ">") + "  MTIME"
    ]
    for p, sid in zip(projects, sids):
        idx = "yes" if p.has_index else "no"
        lines.append(
            _pad(sid, proj_w) + "  " + _pad(str(p.entry_count), 5, ">") + "  "
            + _pad(idx, 6, ">") + "  " + _fmt_mtime(p.mtime)
        )
    return "\n".join(lines)


def fmt_list(entries: list[MemoryEntry], short_id_fn: ShortIdFn, *, json_mode: bool = False) -> str:
    if json_mode:
        items = [{
            "ref": f"{short_id_fn(e.project_id)}:{e.filename}",
            "project_id": e.project_id,
            "project_short": short_id_fn(e.project_id),
            "filename": e.filename,
            "name": e.name,
            "description": e.description,
            "type": e.type,
            "origin_session_id": e.origin_session_id,
            "file_path": str(e.file_path),
            "mtime": e.mtime,
        } for e in entries]
        return json.dumps({"items": items, "errors": []}, ensure_ascii=False, indent=2)
    if not entries:
        return "(no entries)"
    refs = [f"{short_id_fn(e.project_id)}:{e.filename}" for e in entries]
    names = [e.name for e in entries]
    ref_w, name_w = _compute_widths(refs, names, _terminal_width())
    lines = [
        _pad("TYPE", 10) + "  " + _pad("REF", ref_w) + "  " + _pad("NAME", name_w) + "  MTIME"
    ]
    for e, ref in zip(entries, refs):
        lines.append(
            _pad(_truncate(e.type, 10), 10) + "  "
            + _pad(_truncate(ref, ref_w), ref_w) + "  "
            + _pad(_truncate(e.name, name_w), name_w) + "  "
            + _fmt_mtime(e.mtime)
        )
    return "\n".join(lines)


def fmt_search(results: list[tuple[MemoryEntry, list[str]]], short_id_fn: ShortIdFn, *, json_mode: bool = False) -> str:
    if json_mode:
        items = [{
            "ref": f"{short_id_fn(e.project_id)}:{e.filename}",
            "project_id": e.project_id,
            "filename": e.filename,
            "name": e.name,
            "description": e.description,
            "type": e.type,
            "matched": matched,
            "file_path": str(e.file_path),
            "mtime": e.mtime,
        } for e, matched in results]
        return json.dumps({"items": items, "errors": []}, ensure_ascii=False, indent=2)
    if not results:
        return "(no matches)"
    lines = []
    for e, matched in results:
        ref = f"{short_id_fn(e.project_id)}:{e.filename}"
        lines.append(f"[{e.type}] {ref}  {e.name}")
        for line in matched:
            lines.append("    " + _truncate(line, 116))
        lines.append("")
    return "\n".join(lines).rstrip()


def fmt_cat(entry: MemoryEntry, *, no_frontmatter: bool = False, json_mode: bool = False) -> str:
    if json_mode:
        return json.dumps({
            "project_id": entry.project_id,
            "project_path": entry.project_path,
            "filename": entry.filename,
            "name": entry.name,
            "description": entry.description,
            "type": entry.type,
            "origin_session_id": entry.origin_session_id,
            "body": entry.body,
            "file_path": str(entry.file_path),
            "mtime": entry.mtime,
        }, ensure_ascii=False, indent=2)
    if no_frontmatter:
        return entry.body
    fm_lines = [
        "---",
        f"name: {entry.name}",
        f"description: {entry.description}",
        f"type: {entry.type}",
    ]
    if entry.origin_session_id:
        fm_lines.append(f"originSessionId: {entry.origin_session_id}")
    fm_lines.append("---")
    return "\n".join(fm_lines) + "\n" + entry.body


def fmt_which(entry: MemoryEntry, *, windows_style: bool = False) -> str:
    p = str(entry.file_path)
    if not windows_style:
        p = p.replace("\\", "/")
    return p


def fmt_ambiguous(err: AmbiguousRefError, short_id_fn: ShortIdFn) -> str:
    lines = [f"Ambiguous ref {err.ref!r} ({len(err.candidates)} candidates):"]
    for e in err.candidates:
        ref = f"{short_id_fn(e.project_id)}:{e.filename}"
        lines.append(f"  {ref}  ({e.name})")
    lines.append("Use the full <project>:<filename> form.")
    return "\n".join(lines)
