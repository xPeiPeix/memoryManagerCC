from __future__ import annotations

import os
import time
from pathlib import Path

import pytest


@pytest.fixture
def mock_projects(tmp_path: Path) -> Path:
    pdir = tmp_path / "projects"
    pdir.mkdir()

    normal = pdir / "D--test-normal"
    normal_mem = normal / "memory"
    normal_mem.mkdir(parents=True)
    (normal_mem / "MEMORY.md").write_text(
        "- [first feedback](feedback_first.md) — desc\n", encoding="utf-8"
    )
    (normal_mem / "feedback_first.md").write_text(
        "---\n"
        "name: First feedback\n"
        "description: A real feedback\n"
        "type: feedback\n"
        "originSessionId: abc-123\n"
        "---\n"
        "**Why:** because of X. shared_keyword in feedback body.\n"
        "**How to apply:** when Y happens.\n",
        encoding="utf-8",
    )
    (normal_mem / "reference_docs.md").write_text(
        "---\n"
        "name: API docs link\n"
        "description: Where to look up\n"
        "type: reference\n"
        "---\n"
        "Visit https://example.com/docs and shared_keyword too.\n",
        encoding="utf-8",
    )

    (pdir / "D--test-empty" / "memory").mkdir(parents=True)

    broken_mem = pdir / "D--test-broken" / "memory"
    broken_mem.mkdir(parents=True)
    (broken_mem / "feedback_broken.md").write_text(
        "---\nname: half written\n", encoding="utf-8"
    )
    (broken_mem / "no_frontmatter.md").write_text(
        "Just a body, no frontmatter\n", encoding="utf-8"
    )

    (pdir / "D--test-no-memory").mkdir()

    utf8_mem = pdir / "C--Users-test-utf8" / "memory"
    utf8_mem.mkdir(parents=True)
    (utf8_mem / "feedback_中文.md").write_text(
        "---\n"
        "name: 中文 feedback\n"
        "description: 测试中文\n"
        "type: feedback\n"
        "---\n"
        "中文 body 内容\n",
        encoding="utf-8",
    )

    worktree = pdir / "D--test-worktree"
    worktree.mkdir()
    try:
        os.symlink(str(normal_mem), str(worktree / "memory"), target_is_directory=True)
    except (OSError, NotImplementedError):
        (worktree / "memory").mkdir()

    unk_mem = pdir / "D--test-unknown" / "memory"
    unk_mem.mkdir(parents=True)
    (unk_mem / "custom_type.md").write_text(
        "---\nname: Custom\ntype: custom_type_here\n---\nbody\n",
        encoding="utf-8",
    )

    _stagger_mtimes(pdir)
    return pdir


@pytest.fixture
def has_symlink_support(mock_projects: Path) -> bool:
    return (mock_projects / "D--test-worktree" / "memory").is_symlink()


def _stagger_mtimes(pdir: Path) -> None:
    now = time.time()
    files_in_order = [
        pdir / "C--Users-test-utf8" / "memory" / "feedback_中文.md",
        pdir / "D--test-unknown" / "memory" / "custom_type.md",
        pdir / "D--test-normal" / "memory" / "reference_docs.md",
        pdir / "D--test-normal" / "memory" / "feedback_first.md",
    ]
    for i, f in enumerate(files_in_order):
        if f.exists():
            ts = now - (len(files_in_order) - i) * 100
            os.utime(f, (ts, ts))
