from __future__ import annotations

from pathlib import Path

import pytest

from mmcc.store import (
    AmbiguousRefError,
    MemoryStore,
    NotFoundError,
    _name_from_filename,
    _parse_frontmatter,
)


class TestParseFrontmatter:
    def test_complete(self):
        text = "---\nname: foo\ntype: feedback\n---\nbody here\n"
        result = _parse_frontmatter(text)
        assert result is not None
        fm, body = result
        assert fm["name"] == "foo"
        assert fm["type"] == "feedback"
        assert body == "body here\n"

    def test_quoted_values(self):
        text = '---\nname: "quoted"\ndescription: \'single\'\n---\nbody\n'
        result = _parse_frontmatter(text)
        assert result is not None
        fm, _ = result
        assert fm["name"] == "quoted"
        assert fm["description"] == "single"

    def test_no_frontmatter(self):
        assert _parse_frontmatter("just body\n") is None

    def test_unclosed_frontmatter(self):
        assert _parse_frontmatter("---\nname: foo\n") is None

    def test_empty(self):
        assert _parse_frontmatter("") is None

    def test_multiline_body(self):
        text = "---\nname: x\n---\nline1\nline2\n"
        result = _parse_frontmatter(text)
        assert result is not None
        _, body = result
        assert "line1" in body and "line2" in body


class TestNameFromFilename:
    def test_feedback_prefix(self):
        assert _name_from_filename("feedback_my_topic.md") == "my topic"

    def test_no_prefix(self):
        assert _name_from_filename("my_topic.md") == "my topic"

    def test_no_extension(self):
        assert _name_from_filename("feedback_x") == "x"


class TestParseEntry:
    def test_complete(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        f = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
        entry = store.parse_entry(f)
        assert entry is not None
        assert entry.name == "First feedback"
        assert entry.type == "feedback"
        assert entry.origin_session_id == "abc-123"
        assert "Why" in entry.body

    def test_unknown_type(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        f = mock_projects / "D--test-unknown" / "memory" / "custom_type.md"
        entry = store.parse_entry(f)
        assert entry is not None
        assert entry.type == "unknown"

    def test_broken_frontmatter(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        f = mock_projects / "D--test-broken" / "memory" / "feedback_broken.md"
        assert store.parse_entry(f) is None

    def test_no_frontmatter(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        f = mock_projects / "D--test-broken" / "memory" / "no_frontmatter.md"
        assert store.parse_entry(f) is None

    def test_nonexistent(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        assert store.parse_entry(mock_projects / "nope.md") is None


class TestListProjects:
    def test_default_excludes_aliases_and_empty(self, mock_projects: Path, has_symlink_support: bool):
        store = MemoryStore(mock_projects)
        ids = {p.project_id for p in store.list_projects()}
        assert "D--test-normal" in ids
        assert "D--test-empty" not in ids
        assert "D--test-no-memory" not in ids
        if has_symlink_support:
            assert "D--test-worktree" not in ids

    def test_include_empty(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        ids = {p.project_id for p in store.list_projects(include_empty=True)}
        assert "D--test-empty" in ids
        assert "D--test-no-memory" not in ids

    def test_include_aliases(self, mock_projects: Path, has_symlink_support: bool):
        if not has_symlink_support:
            pytest.skip("symlink unavailable")
        store = MemoryStore(mock_projects)
        projects = store.list_projects(include_aliases=True)
        ids = {p.project_id for p in projects}
        assert "D--test-worktree" in ids
        worktree = next(p for p in projects if p.project_id == "D--test-worktree")
        assert worktree.is_alias

    def test_entry_count(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        normal = next(p for p in store.list_projects() if p.project_id == "D--test-normal")
        assert normal.entry_count == 2
        assert normal.has_index is True

    def test_empty_dir(self, tmp_path):
        store = MemoryStore(tmp_path / "does-not-exist")
        assert store.list_projects() == []


class TestListEntries:
    def test_all(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        entries = store.list_entries()
        names = {e.name for e in entries}
        assert "First feedback" in names
        assert "API docs link" in names
        assert "中文 feedback" in names

    def test_filter_by_project(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        entries = store.list_entries(project_id="D--test-normal")
        assert len(entries) == 2
        assert all(e.project_id == "D--test-normal" for e in entries)

    def test_filter_by_type(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        entries = store.list_entries(type_filter="feedback")
        assert all(e.type == "feedback" for e in entries)
        assert len(entries) >= 2

    def test_skips_broken(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        entries = store.list_entries()
        bad = {"feedback_broken.md", "no_frontmatter.md"}
        assert not any(e.filename in bad for e in entries)

    def test_mtime_descending(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        mtimes = [e.mtime for e in store.list_entries()]
        assert mtimes == sorted(mtimes, reverse=True)


class TestSearch:
    def test_keyword_in_body(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        results = store.search("shared_keyword")
        assert len(results) >= 2

    def test_keyword_in_name(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        results = store.search("API docs")
        assert any(e.name == "API docs link" for e, _ in results)

    def test_case_insensitive_default(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        upper = store.search("WHY")
        lower = store.search("why")
        assert len(upper) == len(lower)

    def test_case_sensitive(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        sensitive = store.search("API DOCS", case_sensitive=True)
        names = {e.name for e, _ in sensitive}
        assert "API docs link" not in names

    def test_feedback_first(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        results = store.search("shared_keyword")
        assert len(results) >= 2
        assert results[0][0].type == "feedback"

    def test_filter_by_type(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        results = store.search("shared_keyword", type_filter="reference")
        assert all(e.type == "reference" for e, _ in results)

    def test_only_title(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        results = store.search("Why", in_title=True, in_body=False, in_description=False)
        names = {e.name for e, _ in results}
        assert "First feedback" not in names

    def test_chinese(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        results = store.search("中文")
        assert any("中文" in e.name for e, _ in results)


class TestFind:
    def test_unique_filename(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        entry = store.find("feedback_first.md")
        assert entry.name == "First feedback"

    def test_filename_without_extension(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        entry = store.find("feedback_first")
        assert entry.name == "First feedback"

    def test_with_project_prefix(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        entry = store.find("D--test-normal:feedback_first.md")
        assert entry.project_id == "D--test-normal"

    def test_not_found(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        with pytest.raises(NotFoundError):
            store.find("does_not_exist.md")

    def test_ambiguous(self, mock_projects: Path):
        dup_mem = mock_projects / "D--test-dup" / "memory"
        dup_mem.mkdir(parents=True)
        (dup_mem / "feedback_first.md").write_text(
            "---\nname: another\ntype: feedback\n---\nother body\n",
            encoding="utf-8",
        )
        store = MemoryStore(mock_projects)
        with pytest.raises(AmbiguousRefError) as exc_info:
            store.find("feedback_first.md")
        assert len(exc_info.value.candidates) == 2


class TestShortId:
    def test_short_id_with_common_prefix(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        prefix = store.common_prefix()
        if prefix:
            short = store.short_id("D--test-normal")
            assert "D--test-normal".startswith(prefix)
            assert short != "D--test-normal" or prefix == ""
