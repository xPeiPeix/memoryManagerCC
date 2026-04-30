from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pytest

from mmcc.cli import main


def _run(argv: Iterable[str], capsys) -> tuple[int, str, str]:
    rc = main(list(argv))
    out, err = capsys.readouterr()
    return rc, out, err


class TestListPositional:
    def test_positional_equivalent_to_flag(self, mock_projects: Path, capsys):
        rc1, out1, _ = _run(
            ["--projects-dir", str(mock_projects), "list", "D--test-normal"],
            capsys,
        )
        rc2, out2, _ = _run(
            ["--projects-dir", str(mock_projects), "list", "--project", "D--test-normal"],
            capsys,
        )
        assert rc1 == 0
        assert rc2 == 0
        assert out1 == out2

    def test_positional_only(self, mock_projects: Path, capsys):
        rc, out, _ = _run(
            ["--projects-dir", str(mock_projects), "list", "D--test-normal"],
            capsys,
        )
        assert rc == 0
        assert "First feedback" in out

    def test_flag_only(self, mock_projects: Path, capsys):
        rc, out, _ = _run(
            ["--projects-dir", str(mock_projects), "list", "--project", "D--test-normal"],
            capsys,
        )
        assert rc == 0
        assert "First feedback" in out

    def test_both_conflict(self, mock_projects: Path):
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--projects-dir", str(mock_projects),
                "list", "D--test-normal", "--project", "D--test-empty",
            ])
        assert exc_info.value.code == 2

    def test_neither_lists_all(self, mock_projects: Path, capsys):
        rc, out, _ = _run(
            ["--projects-dir", str(mock_projects), "list"],
            capsys,
        )
        assert rc == 0
        assert "First feedback" in out

    def test_short_id_works_as_positional(self, mock_projects: Path, capsys):
        rc, out, _ = _run(
            ["--projects-dir", str(mock_projects), "list", "test-normal"],
            capsys,
        )
        assert rc == 0
        assert "First feedback" in out


class TestSearchPositional:
    def test_positional_equivalent_to_flag(self, mock_projects: Path, capsys):
        rc1, out1, _ = _run(
            ["--projects-dir", str(mock_projects), "search", "shared_keyword", "D--test-normal"],
            capsys,
        )
        rc2, out2, _ = _run(
            ["--projects-dir", str(mock_projects), "search", "shared_keyword", "--project", "D--test-normal"],
            capsys,
        )
        assert rc1 == 0
        assert rc2 == 0
        assert out1 == out2

    def test_positional_only(self, mock_projects: Path, capsys):
        rc, out, _ = _run(
            ["--projects-dir", str(mock_projects), "search", "shared_keyword", "D--test-normal"],
            capsys,
        )
        assert rc == 0
        assert "shared_keyword" in out or "First feedback" in out

    def test_both_conflict(self, mock_projects: Path):
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--projects-dir", str(mock_projects),
                "search", "shared_keyword", "D--test-normal",
                "--project", "D--test-empty",
            ])
        assert exc_info.value.code == 2

    def test_no_project(self, mock_projects: Path, capsys):
        rc, out, _ = _run(
            ["--projects-dir", str(mock_projects), "search", "shared_keyword"],
            capsys,
        )
        assert rc == 0


class TestSearchAllWords:
    def test_all_words_flag(self, mock_projects: Path, capsys):
        rc, out, _ = _run(
            ["--projects-dir", str(mock_projects),
             "search", "shared_keyword Why", "--all-words"],
            capsys,
        )
        assert rc == 0
        assert "First feedback" in out
        assert "API docs link" not in out

    def test_all_words_partial_skip(self, mock_projects: Path, capsys):
        rc, out, _ = _run(
            ["--projects-dir", str(mock_projects),
             "search", "shared_keyword nonexistent_xyz", "--all-words"],
            capsys,
        )
        assert rc == 0
        assert "(no matches)" in out
