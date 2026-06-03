# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from operations_center.execution.validation import (
    EnvironmentCheck,
    ImproveTriageResult,
    _check_execution_environment,
    _collect_open_pr_files,
    _has_conflict_with_active_task,
    build_improve_triage_result,
)


# ── _check_execution_environment ─────────────────────────────────────────────


def test_check_env_missing_workspace(tmp_path):
    missing = tmp_path / "does-not-exist"
    res = _check_execution_environment(missing)
    assert isinstance(res, EnvironmentCheck)
    assert res.ok is False
    assert res.missing == ("workspace_path",)
    assert res.notes == ()


def test_check_env_no_git_and_empty(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    res = _check_execution_environment(ws)
    assert res.ok is False
    assert ".git" in res.missing
    assert "workspace_is_empty" in res.notes


def test_check_env_happy_path(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".git").mkdir()
    (ws / "README.md").write_text("hi")
    res = _check_execution_environment(ws, required_files=("README.md",))
    assert res.ok is True
    assert res.missing == ()
    assert res.notes == ()


def test_check_env_missing_required_file(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".git").mkdir()
    res = _check_execution_environment(ws, required_files=("pyproject.toml",))
    assert res.ok is False
    assert "pyproject.toml" in res.missing
    # .git exists so not flagged; workspace not empty so no note
    assert ".git" not in res.missing
    assert res.notes == ()


def test_check_env_accepts_string_path(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".git").mkdir()
    res = _check_execution_environment(str(ws))
    assert res.ok is True


def test_check_env_git_present_but_empty_dir_note(tmp_path):
    # .git present means not empty, so the empty-note branch needs a dir with
    # no .git. Verify empty-note appears alongside missing .git.
    ws = tmp_path / "ws"
    ws.mkdir()
    res = _check_execution_environment(ws)
    assert res.ok is False
    assert res.missing == (".git",)
    assert res.notes == ("workspace_is_empty",)


# ── _collect_open_pr_files ───────────────────────────────────────────────────


def test_collect_pr_files_happy():
    gh = MagicMock()
    gh.list_open_prs.return_value = [{"number": 1}, {"number": 2}]
    gh.list_pr_files.side_effect = lambda o, r, n: [f"file{n}.py"]
    out = _collect_open_pr_files(gh, "owner", "repo")
    assert out == {1: ["file1.py"], 2: ["file2.py"]}


def test_collect_pr_files_list_open_prs_fails():
    gh = MagicMock()
    gh.list_open_prs.side_effect = RuntimeError("boom")
    out = _collect_open_pr_files(gh, "o", "r")
    assert out == {}


def test_collect_pr_files_skips_excluded_pr():
    gh = MagicMock()
    gh.list_open_prs.return_value = [{"number": 1}, {"number": 2}]
    gh.list_pr_files.return_value = ["x.py"]
    out = _collect_open_pr_files(gh, "o", "r", exclude_pr=1)
    assert 1 not in out
    assert out == {2: ["x.py"]}


def test_collect_pr_files_skips_none_number():
    gh = MagicMock()
    gh.list_open_prs.return_value = [{"title": "no number"}, {"number": 3}]
    gh.list_pr_files.return_value = ["a.py"]
    out = _collect_open_pr_files(gh, "o", "r")
    assert out == {3: ["a.py"]}


def test_collect_pr_files_per_pr_fetch_failure_dropped():
    gh = MagicMock()
    gh.list_open_prs.return_value = [{"number": 5}]
    gh.list_pr_files.side_effect = ValueError("nope")
    out = _collect_open_pr_files(gh, "o", "r")
    # files = [] so PR not added
    assert out == {}


def test_collect_pr_files_empty_files_omitted():
    gh = MagicMock()
    gh.list_open_prs.return_value = [{"number": 7}]
    gh.list_pr_files.return_value = []
    out = _collect_open_pr_files(gh, "o", "r")
    assert out == {}


def test_collect_pr_files_coerces_number_to_int():
    gh = MagicMock()
    gh.list_open_prs.return_value = [{"number": "9"}]
    gh.list_pr_files.return_value = ["z.py"]
    out = _collect_open_pr_files(gh, "o", "r")
    assert out == {9: ["z.py"]}


# ── _has_conflict_with_active_task ───────────────────────────────────────────


def test_conflict_empty_candidate():
    has, prs = _has_conflict_with_active_task([], {1: ["a.py"]})
    assert has is False
    assert prs == []


def test_conflict_candidate_all_falsy():
    has, prs = _has_conflict_with_active_task(["", None], {1: ["a.py"]})
    assert has is False
    assert prs == []


def test_conflict_detected():
    has, prs = _has_conflict_with_active_task(
        ["src/a.py"], {3: ["src/a.py", "src/b.py"], 4: ["src/c.py"]}
    )
    assert has is True
    assert prs == [3]


def test_conflict_multiple_sorted():
    has, prs = _has_conflict_with_active_task(["a.py"], {9: ["a.py"], 2: ["a.py"], 5: ["other.py"]})
    assert has is True
    assert prs == [2, 9]


def test_conflict_no_overlap():
    has, prs = _has_conflict_with_active_task(["a.py"], {1: ["b.py"]})
    assert has is False
    assert prs == []


def test_conflict_excludes_in_review_pr():
    has, prs = _has_conflict_with_active_task(["a.py"], {1: ["a.py"]}, in_review_pr=1)
    assert has is False
    assert prs == []


def test_conflict_path_normalization():
    # './src/a.py' normalizes to 'src/a.py'
    has, prs = _has_conflict_with_active_task(["./src/a.py"], {1: ["src/a.py"]})
    assert has is True
    assert prs == [1]


def test_conflict_skips_falsy_files_in_pr():
    has, prs = _has_conflict_with_active_task(["a.py"], {1: ["", None, "a.py"]})
    assert has is True
    assert prs == [1]


# ── build_improve_triage_result ──────────────────────────────────────────────


def test_build_triage_happy(tmp_path):
    res = build_improve_triage_result(
        success=True,
        summary="all good",
        suggestions=[{"title": "do x"}, {"title": "do y"}],
        workspace_path=tmp_path / "ws",
        executor_exit_code=0,
    )
    assert isinstance(res, ImproveTriageResult)
    assert res.success is True
    assert res.summary == "all good"
    assert res.suggestions == ({"title": "do x"}, {"title": "do y"})
    assert res.workspace_path == str(tmp_path / "ws")
    assert res.executor_exit_code == 0


def test_build_triage_none_suggestions():
    res = build_improve_triage_result(
        success=False,
        summary="",
        suggestions=None,
        workspace_path="/tmp/x",
        executor_exit_code=1,
    )
    assert res.suggestions == ()
    assert res.success is False
    assert res.summary == ""


def test_build_triage_filters_invalid_suggestions():
    res = build_improve_triage_result(
        success=True,
        summary="s",
        suggestions=[
            {"title": "keep"},
            {"no_title": 1},  # missing title
            {"title": ""},  # falsy title
            "not a dict",  # wrong type
            None,
        ],
        workspace_path="ws",
        executor_exit_code=0,
    )
    assert res.suggestions == ({"title": "keep"},)


def test_build_triage_summary_truncated():
    long = "x" * 1000
    res = build_improve_triage_result(
        success=True,
        summary=long,
        suggestions=[],
        workspace_path="ws",
        executor_exit_code=0,
    )
    assert len(res.summary) == 500


def test_build_triage_summary_none_coerced():
    res = build_improve_triage_result(
        success=True,
        summary=None,  # type: ignore[arg-type]
        suggestions=[],
        workspace_path="ws",
        executor_exit_code=0,
    )
    assert res.summary == ""


def test_build_triage_coerces_types():
    res = build_improve_triage_result(
        success=1,  # type: ignore[arg-type]
        summary=123,  # type: ignore[arg-type]
        suggestions=[],
        workspace_path=Path("/a/b"),
        executor_exit_code="5",  # type: ignore[arg-type]
    )
    assert res.success is True
    assert res.summary == "123"
    assert res.executor_exit_code == 5
    assert res.workspace_path == str(Path("/a/b"))


def test_dataclasses_are_frozen():
    res = build_improve_triage_result(
        success=True,
        summary="s",
        suggestions=[],
        workspace_path="ws",
        executor_exit_code=0,
    )
    import pytest

    with pytest.raises(Exception):
        res.success = False  # type: ignore[misc]
    env = EnvironmentCheck(ok=True, missing=(), notes=())
    with pytest.raises(Exception):
        env.ok = False  # type: ignore[misc]
