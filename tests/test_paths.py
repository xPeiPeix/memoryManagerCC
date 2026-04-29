from __future__ import annotations

import os

import pytest

from mmcc.paths import (
    decode_project_dir,
    is_worktree_alias,
    longest_common_prefix,
    normalize,
    project_short_id,
    real_memory_dir,
)


class TestDecodeProjectDir:
    def test_drive_uppercase(self):
        assert decode_project_dir("D--dev-code-x") == "D:/dev/code/x"

    def test_drive_lowercase(self):
        assert decode_project_dir("d--dev-code-x") == "d:/dev/code/x"

    def test_drive_users(self):
        assert decode_project_dir("C--Users-peipei") == "C:/Users/peipei"

    def test_wsl_prefix(self):
        assert decode_project_dir("-mnt-d-dev-code") == "/mnt/d/dev/code"

    def test_posix_prefix(self):
        assert decode_project_dir("-Users-x-project") == "/Users/x/project"

    def test_empty(self):
        assert decode_project_dir("") is None

    def test_unknown_prefix(self):
        assert decode_project_dir("random_name") is None

    def test_long_path(self):
        result = decode_project_dir("D--dev-code-AI-related-claude-repath")
        assert result == "D:/dev/code/AI/related/claude/repath"


class TestNormalize:
    def test_absolute_path_resolved(self, tmp_path):
        result = normalize(tmp_path)
        assert result.is_absolute()

    def test_git_bash_to_windows_form(self):
        result = normalize("/d/foo/bar")
        s = str(result).replace("\\", "/")
        assert "d:" in s.lower()

    def test_user_expansion(self):
        result = normalize("~")
        assert result.is_absolute()

    def test_nonexistent_path(self):
        result = normalize("/this/path/does/not/exist/xyz")
        assert result.is_absolute()


class TestRealMemoryDir:
    def test_existing(self, tmp_path):
        (tmp_path / "memory").mkdir()
        result = real_memory_dir(tmp_path)
        assert result is not None
        assert result.name == "memory"

    def test_missing(self, tmp_path):
        assert real_memory_dir(tmp_path) is None


class TestIsWorktreeAlias:
    def test_symlink_is_alias(self, tmp_path):
        target = tmp_path / "real"
        target.mkdir()
        (target / "memory").mkdir()
        alias = tmp_path / "alias"
        alias.mkdir()
        try:
            os.symlink(str(target / "memory"), str(alias / "memory"), target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink unavailable on this platform/permission")
        assert is_worktree_alias(alias) is True

    def test_regular_dir_not_alias(self, tmp_path):
        (tmp_path / "memory").mkdir()
        assert is_worktree_alias(tmp_path) is False

    def test_no_memory_dir(self, tmp_path):
        assert is_worktree_alias(tmp_path) is False


class TestProjectShortId:
    def test_with_common_prefix(self):
        assert project_short_id("D--dev-code-x", "D--dev-code-") == "x"

    def test_no_match(self):
        assert project_short_id("foo", "D--dev-code-") == "foo"

    def test_empty_prefix(self):
        assert project_short_id("foo", "") == "foo"

    def test_full_match(self):
        assert project_short_id("D--dev-code-", "D--dev-code-") == "D--dev-code-"


class TestLongestCommonPrefix:
    def test_empty(self):
        assert longest_common_prefix([]) == ""

    def test_single(self):
        assert longest_common_prefix(["abc"]) == "abc"

    def test_common(self):
        assert longest_common_prefix(["D--dev-x", "D--dev-y", "D--dev-z"]) == "D--dev-"

    def test_no_common(self):
        assert longest_common_prefix(["abc", "xyz"]) == ""
