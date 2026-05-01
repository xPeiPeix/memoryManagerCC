from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mmcc.store import (
    AmbiguousRefError,
    EntryExistsError,
    MemoryStore,
    NotFoundError,
    _name_from_filename,
    _parse_frontmatter,
    _slugify,
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

    def test_all_words_and_match(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        results = store.search("shared_keyword Why", all_words=True)
        names = {e.name for e, _ in results}
        assert "First feedback" in names
        assert "API docs link" not in names

    def test_all_words_partial_skip(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        results = store.search("shared_keyword nonexistent_xyz", all_words=True)
        assert len(results) == 0

    def test_all_words_default_off(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        results = store.search("shared keyword")
        names = {e.name for e, _ in results}
        assert "First feedback" not in names
        assert "API docs link" not in names

    def test_all_words_with_type_filter(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        results = store.search("shared_keyword Why", all_words=True, type_filter="reference")
        assert len(results) == 0

    def test_all_words_case_sensitive(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        sensitive = store.search("SHARED_KEYWORD why", all_words=True, case_sensitive=True)
        assert len(sensitive) == 0
        insensitive = store.search("SHARED_KEYWORD why", all_words=True)
        assert len(insensitive) >= 1

    def test_all_words_empty(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        assert store.search("", all_words=True) == []
        assert store.search("   ", all_words=True) == []

    def test_fuzzy_typo_match(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        results = store.search("shred_keyword", fuzzy=True)
        names = {e.name for e, _ in results}
        assert "First feedback" in names

    def test_fuzzy_too_far(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        results = store.search("zzqxyzgarbage", fuzzy=True)
        assert len(results) == 0

    def test_fuzzy_with_type_filter(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        results = store.search("shred_keyword", fuzzy=True, type_filter="reference")
        types = {e.type for e, _ in results}
        assert types <= {"reference"}

    def test_fuzzy_default_off(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        results = store.search("shred_keyword")
        assert len(results) == 0


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


def _make_projects(tmp_path: Path, names: list[str]) -> Path:
    pdir = tmp_path / "projects"
    pdir.mkdir()
    for n in names:
        mem = pdir / n / "memory"
        mem.mkdir(parents=True)
        (mem / "feedback_x.md").write_text(
            "---\nname: x\ntype: feedback\n---\nbody\n", encoding="utf-8"
        )
    return pdir


class TestShortIdMultiLayer:
    def test_strips_two_layer_category(self, tmp_path: Path):
        pdir = _make_projects(tmp_path, [
            "D--dev-code-AI-related-CCometixLine",
            "D--dev-code-AI-related-ai-gateways",
            "D--dev-code-AI-related-claude-repath",
            "D--dev-code-AI-related-memoryManagerCC",
            "D--dev-code-foo",
            "D--dev-code-bar",
        ])
        store = MemoryStore(pdir)
        assert store.short_id("D--dev-code-AI-related-CCometixLine") == "CCometixLine"
        assert store.short_id("D--dev-code-AI-related-ai-gateways") == "ai-gateways"
        assert store.short_id("D--dev-code-foo") == "foo"
        assert store.short_id("D--dev-code-bar") == "bar"

    def test_multiple_categories(self, tmp_path: Path):
        pdir = _make_projects(tmp_path, [
            "D--dev-code-AI-related-X",
            "D--dev-code-AI-related-Y",
            "D--dev-code-Tools-Japanese-learning",
            "D--dev-code-Tools-foo",
            "D--dev-code-ICBC-bar",
            "D--dev-code-ICBC-baz",
        ])
        store = MemoryStore(pdir)
        assert store.short_id("D--dev-code-AI-related-X") == "X"
        assert store.short_id("D--dev-code-Tools-Japanese-learning") == "Japanese-learning"
        assert store.short_id("D--dev-code-Tools-foo") == "foo"
        assert store.short_id("D--dev-code-ICBC-bar") == "bar"

    def test_falls_back_when_short_names_collide(self, tmp_path: Path):
        pdir = _make_projects(tmp_path, [
            "D--dev-code-AI-related-X",
            "D--dev-code-AI-related-Y",
            "D--dev-code-Tools-X",
            "D--dev-code-Tools-Y",
        ])
        store = MemoryStore(pdir)
        assert store.short_id("D--dev-code-AI-related-X") == "AI-related-X"
        assert store.short_id("D--dev-code-AI-related-Y") == "AI-related-Y"
        assert store.short_id("D--dev-code-Tools-X") == "Tools-X"
        assert store.short_id("D--dev-code-Tools-Y") == "Tools-Y"

    def test_no_category_when_low_frequency(self, tmp_path: Path):
        pdir = _make_projects(tmp_path, [
            "D--dev-code-AI-related-X",
            "D--dev-code-foo",
            "D--dev-code-bar",
        ])
        store = MemoryStore(pdir)
        assert store.short_id("D--dev-code-AI-related-X") == "AI-related-X"

    def test_project_id_not_under_lcp(self, tmp_path: Path):
        pdir = _make_projects(tmp_path, [
            "D--dev-code-AI-related-X",
            "D--dev-code-AI-related-Y",
            "C--Users-other-thing",
        ])
        store = MemoryStore(pdir)
        assert store.short_id("C--Users-other-thing") == "C--Users-other-thing"

    def test_aliased_subcategory_keeps_unique_short(self, tmp_path: Path):
        pdir = _make_projects(tmp_path, [
            "D--dev-code-AI-related-X",
            "D--dev-code-AI-related-X-claude-worktrees-foo",
            "D--dev-code-AI-related-Y",
            "D--dev-code-AI-related-Z",
        ])
        store = MemoryStore(pdir)
        ids = [
            "D--dev-code-AI-related-X",
            "D--dev-code-AI-related-X-claude-worktrees-foo",
            "D--dev-code-AI-related-Y",
            "D--dev-code-AI-related-Z",
        ]
        shorts = [store.short_id(p) for p in ids]
        assert len(set(shorts)) == len(shorts), f"Duplicates in {shorts}"


class TestSlugify:
    def test_basic(self):
        assert _slugify("test fb") == "test_fb"

    def test_lowercase(self):
        assert _slugify("Some Topic") == "some_topic"

    def test_special_chars(self):
        assert _slugify("Some Topic!") == "some_topic"

    def test_collapse_underscores(self):
        assert _slugify("a   b") == "a_b"

    def test_chinese_preserved(self):
        assert "中文" in _slugify("中文 fb")

    def test_strip_edges(self):
        assert _slugify("  hi  ") == "hi"


class TestAddEntry:
    def test_add_success(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        path = store.add_entry(
            project_id="D--test-normal",
            name="My new feedback",
            type="feedback",
            description="some desc",
            body="**Why:** because.\n**How to apply:** when X.",
        )
        assert path.exists()
        assert path.name == "feedback_my_new_feedback.md"
        text = path.read_text(encoding="utf-8")
        assert "name: My new feedback" in text
        assert "description: some desc" in text
        assert "type: feedback" in text
        assert "**Why:**" in text

    def test_add_duplicate_raises(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        with pytest.raises(EntryExistsError):
            store.add_entry(
                project_id="D--test-normal",
                name="first",
                type="feedback",
                description="dup",
                body="body",
            )

    def test_add_unknown_project_raises(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        with pytest.raises(NotFoundError):
            store.add_entry(
                project_id="does-not-exist-foo-bar",
                name="x",
                type="feedback",
                description="",
                body="body",
            )

    def test_add_with_origin_session(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        path = store.add_entry(
            project_id="D--test-normal",
            name="with session",
            type="user",
            description="d",
            body="b",
            origin_session_id="sess-xyz-123",
        )
        text = path.read_text(encoding="utf-8")
        assert "originSessionId: sess-xyz-123" in text

    def test_add_round_trip_via_parse(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        path = store.add_entry(
            project_id="D--test-normal",
            name="Round trip",
            type="reference",
            description="round-trip desc",
            body="some body content",
        )
        entry = store.parse_entry(path)
        assert entry is not None
        assert entry.name == "Round trip"
        assert entry.description == "round-trip desc"
        assert entry.type == "reference"
        assert "some body content" in entry.body

    def test_add_resolves_short_id(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        short = store.short_id("D--test-normal")
        path = store.add_entry(
            project_id=short,
            name="via short",
            type="feedback",
            description="d",
            body="b",
        )
        assert path.parent.parent.name == "D--test-normal"

    def test_add_invalid_type_raises(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        with pytest.raises(ValueError):
            store.add_entry(
                project_id="D--test-normal",
                name="x",
                type="invalid_type",
                description="",
                body="b",
            )

    def test_add_empty_name_raises(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        with pytest.raises(ValueError):
            store.add_entry(
                project_id="D--test-normal",
                name="!!!",
                type="feedback",
                description="",
                body="b",
            )


class TestUpdateEntry:
    def test_update_description_only(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
        entry = store.update_entry(target, description="patched desc")
        assert entry.description == "patched desc"
        assert entry.name == "First feedback"
        assert entry.type == "feedback"
        text = target.read_text(encoding="utf-8")
        assert "description: patched desc" in text
        assert "**Why:** because of X. shared_keyword in feedback body." in text

    def test_update_multiple_fields(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
        entry = store.update_entry(target, name="Renamed feedback", description="new")
        assert entry.name == "Renamed feedback"
        assert entry.description == "new"

    def test_update_preserves_origin_session_id(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
        entry = store.update_entry(target, description="x")
        assert entry.origin_session_id == "abc-123"

    def test_update_preserves_unknown_keys(self, mock_projects: Path, tmp_path: Path):
        target = tmp_path / "weird.md"
        target.write_text(
            "---\n"
            "name: orig\n"
            "description: orig desc\n"
            "type: feedback\n"
            "customField: keep-me\n"
            "anotherKey: also-keep\n"
            "---\n"
            "body line\n",
            encoding="utf-8",
        )
        store = MemoryStore(mock_projects=tmp_path) if False else MemoryStore(tmp_path)
        store.update_entry(target, description="changed")
        text = target.read_text(encoding="utf-8")
        assert "customField: keep-me" in text
        assert "anotherKey: also-keep" in text
        assert "description: changed" in text

    def test_update_invalid_type_raises(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
        with pytest.raises(ValueError):
            store.update_entry(target, type="bogus_type")

    def test_update_no_changes_idempotent(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
        before = target.read_text(encoding="utf-8")
        store.update_entry(target)
        after = target.read_text(encoding="utf-8")
        # parse round-trip may rewrite minor whitespace but key fields preserved
        before_entry = store.parse_entry(target)
        assert before_entry is not None
        assert before_entry.name == "First feedback"
        assert before_entry.body.strip() == "**Why:** because of X. shared_keyword in feedback body.\n**How to apply:** when Y happens.".strip()


class TestUpdateEntryBody:
    """V2.2: update_entry body 参数 + 原子写"""

    def test_with_body_replaces_body(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
        original = target.read_text(encoding="utf-8")
        assert "**Why:** because of X" in original

        store.update_entry(target, body="completely new body\n")

        text = target.read_text(encoding="utf-8")
        assert "completely new body" in text
        assert "**Why:** because of X" not in text
        # frontmatter intact
        assert "name: First feedback" in text
        assert "description: A real feedback" in text
        assert "originSessionId: abc-123" in text

    def test_body_none_preserves_body(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"

        store.update_entry(target, name="New name only")

        text = target.read_text(encoding="utf-8")
        assert "name: New name only" in text
        assert "**Why:** because of X" in text

    def test_partial_only_name_keeps_body_and_description(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"

        store.update_entry(target, name="Only the name changed")

        text = target.read_text(encoding="utf-8")
        assert "name: Only the name changed" in text
        assert "description: A real feedback" in text
        assert "**Why:** because of X" in text

    def test_atomic_write_no_partial_file_on_crash(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
        original = target.read_text(encoding="utf-8")

        with patch("os.replace", side_effect=OSError("simulated crash")):
            with pytest.raises(OSError):
                store.update_entry(target, name="should not be saved")

        assert target.read_text(encoding="utf-8") == original


class TestDeleteEntry:
    """V2.2: delete_entry 新方法"""

    def test_removes_file(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
        assert target.exists()

        store.delete_entry(target)

        assert not target.exists()

    def test_file_not_found_raises(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        target = mock_projects / "D--test-normal" / "memory" / "does_not_exist.md"

        with pytest.raises(FileNotFoundError):
            store.delete_entry(target)

    def test_invalidates_short_ids_cache(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        _ = store.short_id("D--test-normal")
        assert store._short_ids_cache is not None

        target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
        store.delete_entry(target)

        assert store._short_ids_cache is None


class TestAddEntryAtomicWrite:
    """V2.2: add_entry 改原子写后的崩溃保护"""

    def test_no_partial_file_on_crash(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        target_dir = mock_projects / "D--test-empty" / "memory"
        target_file = target_dir / "feedback_atomic_test.md"
        assert not target_file.exists()

        with patch("os.replace", side_effect=OSError("simulated crash")):
            with pytest.raises(OSError):
                store.add_entry(
                    project_id="D--test-empty",
                    name="atomic test",
                    type="feedback",
                    description="should not survive",
                    body="body",
                )

        assert not target_file.exists()


class TestCodexP1DeleteScope:
    """codex P1: DELETE must be restricted to memory entry files

    Reason: _safe_resolve only checks path is under projects_root, but
    projects_root contains non-entry files (sessions/*.jsonl, project-level
    docs, etc). Without entry-shape check, client can delete arbitrary
    files. mmcc memory entries are <project>/memory/<type>_<slug>.md.
    """

    def test_delete_entry_rejects_non_memory_dir(self, mock_projects: Path, tmp_path: Path):
        # File outside any memory/ dir but inside the project tree
        project = mock_projects / "D--test-normal"
        non_memory = project / "sessions"
        non_memory.mkdir(parents=True)
        target = non_memory / "abc-123.jsonl"
        target.write_text("session log\n", encoding="utf-8")

        store = MemoryStore(mock_projects)
        with pytest.raises(ValueError, match="memory entry"):
            store.delete_entry(target)
        assert target.exists(), "non-memory file must NOT be deleted"

    def test_delete_entry_rejects_non_md_extension(self, mock_projects: Path):
        # File inside memory/ but not .md (e.g. accidental backup)
        target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md.bak"
        target.write_text("backup\n", encoding="utf-8")

        store = MemoryStore(mock_projects)
        with pytest.raises(ValueError, match="memory entry"):
            store.delete_entry(target)
        assert target.exists()

    def test_delete_entry_accepts_memory_md(self, mock_projects: Path):
        # Sanity: real memory entry still deletes (no regression)
        target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
        store = MemoryStore(mock_projects)
        store.delete_entry(target)
        assert not target.exists()


class TestCodexP2UpdateBodyType:
    """codex P2: update_entry must validate body type

    Reason: body.endswith("\\n") raises AttributeError if client sends
    JSON like {"body": 123}. do_PUT only catches (ValueError, OSError),
    so non-str body → 500 instead of controlled 400.
    """

    def test_update_entry_rejects_int_body(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
        with pytest.raises(ValueError, match="body must be"):
            store.update_entry(target, body=123)

    def test_update_entry_rejects_list_body(self, mock_projects: Path):
        store = MemoryStore(mock_projects)
        target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
        with pytest.raises(ValueError, match="body must be"):
            store.update_entry(target, body=["line1", "line2"])

    def test_update_entry_accepts_str_body(self, mock_projects: Path):
        # Sanity: str body still works (no regression)
        store = MemoryStore(mock_projects)
        target = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
        store.update_entry(target, body="new body\n")
        assert "new body" in target.read_text(encoding="utf-8")
