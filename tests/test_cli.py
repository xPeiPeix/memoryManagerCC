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


class TestSearchFuzzy:
    def test_fuzzy_flag(self, mock_projects: Path, capsys):
        rc, out, _ = _run(
            ["--projects-dir", str(mock_projects),
             "search", "shred_keyword", "--fuzzy"],
            capsys,
        )
        assert rc == 0
        assert "First feedback" in out

    def test_fuzzy_no_match(self, mock_projects: Path, capsys):
        rc, out, _ = _run(
            ["--projects-dir", str(mock_projects),
             "search", "zzqxyzgarbage", "--fuzzy"],
            capsys,
        )
        assert rc == 0
        assert "(no matches)" in out

    def test_fuzzy_and_all_words_conflict(self, mock_projects: Path):
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--projects-dir", str(mock_projects),
                "search", "anything", "--fuzzy", "--all-words",
            ])
        assert exc_info.value.code == 2


class TestAddCommand:
    def test_add_with_inline_body(self, mock_projects: Path, capsys):
        rc, out, _ = _run([
            "--projects-dir", str(mock_projects),
            "add",
            "--type", "feedback",
            "--name", "cli new fb",
            "--description", "from cli",
            "--body", "**Why:** test\n**How to apply:** when CLI test runs.",
            "--project", "D--test-normal",
        ], capsys)
        assert rc == 0
        assert "Created:" in out
        assert (mock_projects / "D--test-normal" / "memory" / "feedback_cli_new_fb.md").exists()

    def test_add_duplicate_exits_1(self, mock_projects: Path, capsys):
        rc, _, err = _run([
            "--projects-dir", str(mock_projects),
            "add",
            "--type", "feedback",
            "--name", "first",
            "--body", "x",
            "--project", "D--test-normal",
        ], capsys)
        assert rc == 1
        assert "exists" in err.lower()

    def test_add_unknown_project_exits_1(self, mock_projects: Path, capsys):
        rc, _, err = _run([
            "--projects-dir", str(mock_projects),
            "add",
            "--type", "feedback",
            "--name", "x",
            "--body", "y",
            "--project", "definitely-not-a-real-project-xyz",
        ], capsys)
        assert rc == 1
        assert "not found" in err.lower()

    def test_add_invalid_type_argparse_error(self, mock_projects: Path):
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--projects-dir", str(mock_projects),
                "add",
                "--type", "bogus",
                "--name", "x",
                "--body", "y",
                "--project", "D--test-normal",
            ])
        assert exc_info.value.code == 2

    def test_add_no_project_no_cwd_match_exits_1(self, mock_projects: Path,
                                                  monkeypatch, tmp_path, capsys):
        unrelated = tmp_path / "unrelated"
        unrelated.mkdir()
        monkeypatch.chdir(unrelated)
        rc, _, err = _run([
            "--projects-dir", str(mock_projects),
            "add",
            "--type", "feedback",
            "--name", "x",
            "--body", "y",
        ], capsys)
        assert rc == 1
        assert "not found" in err.lower() or "could not detect" in err.lower()

    def test_add_with_origin_session_id(self, mock_projects: Path, capsys):
        rc, _, _ = _run([
            "--projects-dir", str(mock_projects),
            "add",
            "--type", "user",
            "--name", "user with sess",
            "--body", "body",
            "--origin-session-id", "ses-789",
            "--project", "D--test-normal",
        ], capsys)
        assert rc == 0
        path = mock_projects / "D--test-normal" / "memory" / "user_user_with_sess.md"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "originSessionId: ses-789" in text


class TestEditFrontmatterFlag:
    def test_edit_description_flag_no_editor(self, mock_projects: Path, capsys):
        rc, out, _ = _run([
            "--projects-dir", str(mock_projects),
            "edit", "feedback_first.md",
            "--description", "patched via cli",
        ], capsys)
        assert rc == 0
        assert "Updated:" in out
        path = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
        text = path.read_text(encoding="utf-8")
        assert "description: patched via cli" in text

    def test_edit_multiple_flags(self, mock_projects: Path, capsys):
        rc, _, _ = _run([
            "--projects-dir", str(mock_projects),
            "edit", "feedback_first.md",
            "--name", "Renamed",
            "--description", "new desc",
        ], capsys)
        assert rc == 0
        path = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
        text = path.read_text(encoding="utf-8")
        assert "name: Renamed" in text
        assert "description: new desc" in text

    def test_edit_type_flag(self, mock_projects: Path, capsys):
        rc, _, _ = _run([
            "--projects-dir", str(mock_projects),
            "edit", "feedback_first.md",
            "--type", "user",
        ], capsys)
        assert rc == 0
        path = mock_projects / "D--test-normal" / "memory" / "feedback_first.md"
        text = path.read_text(encoding="utf-8")
        assert "type: user" in text

    def test_edit_invalid_type_argparse_error(self, mock_projects: Path):
        with pytest.raises(SystemExit) as exc_info:
            main([
                "--projects-dir", str(mock_projects),
                "edit", "feedback_first.md",
                "--type", "bogus",
            ])
        assert exc_info.value.code == 2

    def test_edit_no_flags_uses_editor(self, mock_projects: Path, monkeypatch, capsys):
        called = {"argv": None}

        def fake_popen(argv, *a, **kw):
            called["argv"] = argv

            class _Fake:
                pid = 1
            return _Fake()

        monkeypatch.setenv("EDITOR", "echo")
        monkeypatch.setattr("mmcc.cli.subprocess.Popen", fake_popen)
        rc = main([
            "--projects-dir", str(mock_projects),
            "edit", "feedback_first.md",
        ])
        assert rc == 0
        assert called["argv"] is not None
        assert called["argv"][0] == "echo"


class TestEditorLaunchErrors:
    def test_prompt_body_handles_filenotfound(self, monkeypatch, capsys):
        from mmcc import cli

        def fake_run(*a, **kw):
            raise FileNotFoundError("simulated missing editor")

        monkeypatch.setenv("EDITOR", "fake-editor-xxx")
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.setattr("mmcc.cli.subprocess.run", fake_run)
        result = cli._prompt_body_via_editor()
        assert result is None
        _, err = capsys.readouterr()
        assert "Failed to launch" in err or "fake-editor-xxx" in err

    def test_prompt_body_handles_oserror(self, monkeypatch, capsys):
        from mmcc import cli

        def fake_run(*a, **kw):
            raise OSError("simulated permission denied")

        monkeypatch.setenv("EDITOR", "denied-editor")
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.setattr("mmcc.cli.subprocess.run", fake_run)
        result = cli._prompt_body_via_editor()
        assert result is None
        _, err = capsys.readouterr()
        assert "Failed to launch" in err

    def test_add_with_invalid_editor_returns_1_no_traceback(self, mock_projects: Path,
                                                              monkeypatch, capsys):
        def fake_run(*a, **kw):
            raise FileNotFoundError("simulated missing editor")

        monkeypatch.setenv("EDITOR", "no-such-editor-xyz")
        monkeypatch.delenv("VISUAL", raising=False)
        monkeypatch.setattr("mmcc.cli.subprocess.run", fake_run)
        rc = main([
            "--projects-dir", str(mock_projects),
            "add",
            "--type", "feedback",
            "--name", "needs editor",
            "--project", "D--test-normal",
        ])
        _, err = capsys.readouterr()
        assert rc == 1
        assert "Traceback" not in err


class TestFilesystemErrors:
    def test_edit_flag_path_handles_oserror(self, mock_projects: Path,
                                             monkeypatch, capsys):
        def fake_update_entry(*a, **kw):
            raise PermissionError("simulated read-only file")

        monkeypatch.setattr("mmcc.store.MemoryStore.update_entry", fake_update_entry)
        rc = main([
            "--projects-dir", str(mock_projects),
            "edit", "feedback_first.md",
            "--description", "patched",
        ])
        _, err = capsys.readouterr()
        assert rc == 1
        assert "Traceback" not in err
        assert "Error" in err

    def test_edit_flag_disk_full_oserror(self, mock_projects: Path,
                                          monkeypatch, capsys):
        def fake_update_entry(*a, **kw):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("mmcc.store.MemoryStore.update_entry", fake_update_entry)
        rc = main([
            "--projects-dir", str(mock_projects),
            "edit", "feedback_first.md",
            "--name", "renamed",
        ])
        _, err = capsys.readouterr()
        assert rc == 1
        assert "Traceback" not in err

    def test_add_handles_oserror(self, mock_projects: Path, monkeypatch, capsys):
        def fake_add_entry(*a, **kw):
            raise PermissionError("simulated read-only project dir")

        monkeypatch.setattr("mmcc.store.MemoryStore.add_entry", fake_add_entry)
        rc = main([
            "--projects-dir", str(mock_projects),
            "add",
            "--type", "feedback",
            "--name", "x",
            "--body", "y",
            "--project", "D--test-normal",
        ])
        _, err = capsys.readouterr()
        assert rc == 1
        assert "Traceback" not in err
        assert "Error" in err

    def test_add_disk_full_oserror(self, mock_projects: Path, monkeypatch, capsys):
        def fake_add_entry(*a, **kw):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("mmcc.store.MemoryStore.add_entry", fake_add_entry)
        rc = main([
            "--projects-dir", str(mock_projects),
            "add",
            "--type", "user",
            "--name", "x",
            "--body", "y",
            "--project", "D--test-normal",
        ])
        _, err = capsys.readouterr()
        assert rc == 1
        assert "Traceback" not in err
