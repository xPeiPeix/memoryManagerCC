from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

PROJECTS_DIR: Path = Path.home() / ".claude" / "projects"


_DRIVE_PREFIX = re.compile(r"^([A-Za-z])--")
_WSL_PREFIX = re.compile(r"^-mnt-([a-z])-")
_POSIX_PREFIX = re.compile(r"^-([A-Za-z][^-]*)-")


def decode_project_dir(name: str) -> Optional[str]:
    if not name:
        return None
    if m := _DRIVE_PREFIX.match(name):
        rest = name[len(m.group(0)):]
        return f"{m.group(1)}:/{rest.replace('-', '/')}"
    if m := _WSL_PREFIX.match(name):
        rest = name[len(m.group(0)):]
        return f"/mnt/{m.group(1)}/{rest.replace('-', '/')}"
    if m := _POSIX_PREFIX.match(name):
        rest = name[len(m.group(0)):]
        return f"/{m.group(1)}/{rest.replace('-', '/')}"
    return None


def normalize(p: Path | str) -> Path:
    s = str(p)
    if len(s) >= 3 and s[0] == "/" and s[2] == "/" and s[1].isalpha():
        s = f"{s[1]}:{s[2:]}"
    return Path(s).expanduser().resolve(strict=False)


def real_memory_dir(project_dir: Path) -> Optional[Path]:
    md = project_dir / "memory"
    if not md.exists():
        return None
    return md.resolve()


def is_worktree_alias(project_dir: Path) -> bool:
    md = project_dir / "memory"
    if not md.exists():
        return False
    if md.is_symlink():
        return True
    try:
        resolved_parent = md.resolve().parent
        actual_parent = project_dir.resolve()
    except OSError:
        return False
    return resolved_parent != actual_parent


def project_short_id(project_id: str, common_prefix: str = "") -> str:
    if common_prefix and project_id.startswith(common_prefix):
        rest = project_id[len(common_prefix):]
        return rest or project_id
    return project_id


def longest_common_prefix(project_ids: list[str]) -> str:
    if not project_ids:
        return ""
    s1 = min(project_ids)
    s2 = max(project_ids)
    for i, c in enumerate(s1):
        if c != s2[i]:
            return s1[:i]
    return s1
