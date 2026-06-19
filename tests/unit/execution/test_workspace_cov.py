# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from operations_center.contracts.enums import ExecutionStatus, ValidationStatus
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult
from operations_center.execution import workspace as ws_mod
from operations_center.execution.workspace import WorkspaceManager


# ── builders ──────────────────────────────────────────────────────────────


def _make_request(workspace_path: Path, **overrides) -> ExecutionRequest:
    data = dict(
        proposal_id="prop-1",
        decision_id="dec-1",
        goal_text="Fix the widget",
        repo_key="acme/widget",
        clone_url="https://github.com/acme/widget.git",
        base_branch="main",
        task_branch="goal/fix-widget",
        workspace_path=workspace_path,
    )
    data.update(overrides)
    return ExecutionRequest(**data)


def _make_result(success: bool = True, **overrides) -> ExecutionResult:
    data = dict(
        run_id="run-12345678-abcd",
        proposal_id="prop-1",
        decision_id="dec-1",
        status=ExecutionStatus.SUCCEEDED if success else ExecutionStatus.FAILED,
        success=success,
    )
    data.update(overrides)
    return ExecutionResult(**data)


def _fake_completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ── __init__ defaults ───────────────────────────────────────────────────────


def test_init_defaults_and_overrides():
    mgr = WorkspaceManager()
    assert mgr._max_files == ws_mod._DEFAULT_MAX_FILES
    assert mgr._max_lines == ws_mod._DEFAULT_MAX_LINES
    assert mgr._bot_name == "Operations Center"
    assert mgr._await_review == set()
    # default lookup returns None for anything
    assert mgr._repo_lookup("anything") is None

    custom = WorkspaceManager(
        github_token="tok",
        await_review_repos={"acme/widget"},
        bot_identity=("Bot", "bot@x"),
        max_files=5,
        max_lines=10,
        repo_settings_lookup=lambda k: "cfg" if k == "acme/widget" else None,
    )
    assert custom._max_files == 5
    assert custom._max_lines == 10
    assert custom._token == "tok"
    assert custom._await_review == {"acme/widget"}
    assert custom._bot_name == "Bot"
    assert custom._bot_email == "bot@x"
    assert custom._repo_lookup("acme/widget") == "cfg"


# ── prepare() ────────────────────────────────────────────────────────────────


def _git_for_prepare():
    git = mock.Mock()
    git.verify_remote_branch_exists.return_value = None
    return git


def test_prepare_non_empty_workspace_raises(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "preexisting.txt").write_text("x", encoding="utf-8")
    mgr = WorkspaceManager(git_client=_git_for_prepare())
    req = _make_request(ws)
    with pytest.raises(RuntimeError, match="not empty"):
        mgr.prepare(req)


def test_prepare_clone_failure_raises(tmp_path):
    ws = tmp_path / "ws"
    mgr = WorkspaceManager(git_client=_git_for_prepare())
    req = _make_request(ws)
    with mock.patch.object(
        ws_mod.subprocess, "run", return_value=_fake_completed(1, stderr="boom")
    ):
        with pytest.raises(RuntimeError, match="git clone failed: boom"):
            mgr.prepare(req)


def test_prepare_clone_failure_falls_back_to_stdout(tmp_path):
    ws = tmp_path / "ws"
    mgr = WorkspaceManager(git_client=_git_for_prepare())
    req = _make_request(ws)
    with mock.patch.object(
        ws_mod.subprocess, "run", return_value=_fake_completed(1, stdout="stdout-err", stderr="")
    ):
        with pytest.raises(RuntimeError, match="git clone failed: stdout-err"):
            mgr.prepare(req)


def test_prepare_happy_path(tmp_path):
    ws = tmp_path / "ws"
    git = _git_for_prepare()
    mgr = WorkspaceManager(git_client=git)
    req = _make_request(ws)
    with mock.patch.object(ws_mod.subprocess, "run", return_value=_fake_completed(0)):
        mgr.prepare(req)
    git.set_identity.assert_called_once()
    # one add_local_exclude per pattern
    assert git.add_local_exclude.call_count == len(ws_mod._LOCAL_EXCLUDE_PATTERNS)
    git.checkout_base.assert_called_once_with(ws, "main")
    git.restore_to_head.assert_called_once_with(ws, ".baseline-validation.json")
    git.create_task_branch.assert_called_once_with(ws, "goal/fix-widget")
    # not self-healing since verify succeeded first time
    git.create_remote_branch_from.assert_not_called()


def test_prepare_raises_when_token_survives_sanitisation(tmp_path):
    """The post-strip verification is a production gate: a residual token in
    .git/config must fail prepare() closed, not slip through to execution."""
    ws = tmp_path / "ws"
    git = _git_for_prepare()
    mgr = WorkspaceManager(git_client=git)
    req = _make_request(ws)

    def _fake_clone(*_a, **_k):
        # Simulate a clone that left an embedded credential behind even after
        # the config rewrite (e.g. rewrite silently no-op'd).
        (ws / ".git").mkdir(parents=True, exist_ok=True)
        (ws / ".git" / "config").write_text(
            '[remote "origin"]\n\turl = https://ghp_leak@github.com/acme/widget.git\n',
            encoding="utf-8",
        )
        return _fake_completed(0)

    with mock.patch.object(ws_mod.subprocess, "run", side_effect=_fake_clone):
        with pytest.raises(RuntimeError, match="token survived"):
            mgr.prepare(req)
    # Failed closed before establishing identity / creating the branch.
    git.set_identity.assert_not_called()
    git.create_task_branch.assert_not_called()


def test_prepare_pre_created_empty_workspace_ok(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    git = _git_for_prepare()
    mgr = WorkspaceManager(git_client=git)
    req = _make_request(ws)
    with mock.patch.object(ws_mod.subprocess, "run", return_value=_fake_completed(0)):
        mgr.prepare(req)
    git.create_task_branch.assert_called_once()


def test_prepare_self_heals_missing_base_branch(tmp_path):
    ws = tmp_path / "ws"
    git = mock.Mock()
    # first verify raises, after heal it returns None
    git.verify_remote_branch_exists.side_effect = [ValueError("missing"), None]
    git.remote_default_branch.return_value = "main"
    mgr = WorkspaceManager(git_client=git)
    req = _make_request(ws, base_branch="autonomy-staging")
    with mock.patch.object(ws_mod.subprocess, "run", return_value=_fake_completed(0)):
        mgr.prepare(req)
    git.create_remote_branch_from.assert_called_once_with(ws, "autonomy-staging", "origin/main")
    assert git.verify_remote_branch_exists.call_count == 2
    git.create_task_branch.assert_called_once()


def test_prepare_self_heal_failure_raises(tmp_path):
    ws = tmp_path / "ws"
    git = mock.Mock()
    git.verify_remote_branch_exists.side_effect = ValueError("missing")
    git.remote_default_branch.side_effect = RuntimeError("no default")
    mgr = WorkspaceManager(git_client=git)
    req = _make_request(ws, base_branch="autonomy-staging")
    with mock.patch.object(ws_mod.subprocess, "run", return_value=_fake_completed(0)):
        with pytest.raises(RuntimeError, match="does not exist on origin"):
            mgr.prepare(req)


def test_prepare_runs_bootstrap_and_baseline(tmp_path):
    ws = tmp_path / "ws"
    git = _git_for_prepare()
    mgr = WorkspaceManager(git_client=git)
    req = _make_request(ws)
    with (
        mock.patch.object(ws_mod.subprocess, "run", return_value=_fake_completed(0)),
        mock.patch.object(mgr, "_maybe_bootstrap") as boot,
        mock.patch.object(mgr, "_run_baseline_validation") as base,
    ):
        mgr.prepare(req)
    boot.assert_called_once_with(ws, req)
    base.assert_called_once_with(ws, req)


# ── finalize() — early returns ───────────────────────────────────────────────


def test_finalize_returns_unchanged_when_not_success(tmp_path):
    mgr = WorkspaceManager()
    req = _make_request(tmp_path / "ws")
    result = _make_result(success=False)
    assert mgr.finalize(req, result) is result


@pytest.mark.parametrize("prefix", ["improve/", "review/"])
def test_finalize_skips_no_push_prefixes(tmp_path, prefix):
    mgr = WorkspaceManager()
    req = _make_request(tmp_path / "ws", task_branch=f"{prefix}thing")
    result = _make_result(success=True)
    assert mgr.finalize(req, result) is result


def test_finalize_returns_when_not_git_repo(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    mgr = WorkspaceManager()
    req = _make_request(ws)
    result = _make_result(success=True)
    # no .git dir
    assert mgr.finalize(req, result) is result


def _git_repo(tmp_path) -> Path:
    ws = tmp_path / "ws"
    (ws / ".git").mkdir(parents=True)
    return ws


# ── finalize() — oversized diff ──────────────────────────────────────────────


def test_finalize_oversized_diff_writes_marker_and_fails(tmp_path):
    ws = _git_repo(tmp_path)
    mgr = WorkspaceManager(max_files=2, max_lines=5)
    req = _make_request(ws)
    result = _make_result(success=True)
    file_list = [f"f{i}.py" for i in range(20)]
    with mock.patch.object(mgr, "_diff_oversized", return_value=(20, 999, file_list)):
        out = mgr.finalize(req, result)
    assert out.branch_pushed is False
    assert out.failure_category == "scope_too_wide"
    assert "diff exceeded soft cap" in out.failure_reason
    assert "(+5 more)" in out.failure_reason  # 20 files, top 15 shown
    marker = ws / "scope-too-wide.json"
    assert marker.exists()
    import json

    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["files"] == file_list
    assert data["n_lines"] == 999


def test_finalize_oversized_diff_marker_write_failure_is_nonfatal(tmp_path):
    ws = _git_repo(tmp_path)
    mgr = WorkspaceManager()
    req = _make_request(ws)
    result = _make_result(success=True)
    with (
        mock.patch.object(mgr, "_diff_oversized", return_value=(3, 4, ["a.py", "b.py"])),
        mock.patch.object(Path, "write_text", side_effect=OSError("disk full")),
    ):
        out = mgr.finalize(req, result)
    assert out.failure_category == "scope_too_wide"
    # few files -> no "(+N more)"
    assert "more)" not in out.failure_reason


# ── finalize() — push paths ──────────────────────────────────────────────────


def _finalize_git_ok():
    git = mock.Mock()
    git.changed_files.return_value = ["a.py"]
    git.commit_all.return_value = True
    git.squash_commits.return_value = False
    return git


def test_finalize_no_new_commits_returns_early(tmp_path):
    ws = _git_repo(tmp_path)
    git = _finalize_git_ok()
    mgr = WorkspaceManager(git_client=git)
    req = _make_request(ws)
    result = _make_result(success=True)
    with (
        mock.patch.object(mgr, "_diff_oversized", return_value=None),
        mock.patch.object(mgr, "_has_new_commits", return_value=False),
    ):
        out = mgr.finalize(req, result)
    assert out is result
    git.commit_all.assert_called_once()
    git.push_branch.assert_not_called()


def test_finalize_no_changed_files_skips_commit(tmp_path):
    ws = _git_repo(tmp_path)
    git = _finalize_git_ok()
    git.changed_files.return_value = []
    mgr = WorkspaceManager(git_client=git)
    req = _make_request(ws)
    result = _make_result(success=True)
    with (
        mock.patch.object(mgr, "_diff_oversized", return_value=None),
        mock.patch.object(mgr, "_has_new_commits", return_value=False),
    ):
        mgr.finalize(req, result)
    git.commit_all.assert_not_called()


def test_finalize_push_regular_no_squash(tmp_path):
    ws = _git_repo(tmp_path)
    git = _finalize_git_ok()
    git.squash_commits.return_value = False
    mgr = WorkspaceManager(git_client=git)
    req = _make_request(ws)
    result = _make_result(success=True)
    with (
        mock.patch.object(mgr, "_diff_oversized", return_value=None),
        mock.patch.object(mgr, "_has_new_commits", return_value=True),
        mock.patch.object(mgr, "_warn_cross_repo_impact") as warn,
        mock.patch.object(mgr, "_maybe_create_pr", return_value="http://pr/1"),
    ):
        out = mgr.finalize(req, result)
    git.push_branch.assert_called_once_with(ws, "goal/fix-widget")
    git.push_branch_force.assert_not_called()
    warn.assert_called_once_with(ws, req)
    assert out.branch_pushed is True
    assert out.branch_name == "goal/fix-widget"
    assert out.pull_request_url == "http://pr/1"


def test_finalize_push_force_when_squashed(tmp_path):
    ws = _git_repo(tmp_path)
    git = _finalize_git_ok()
    git.squash_commits.return_value = True
    mgr = WorkspaceManager(git_client=git)
    req = _make_request(ws)
    result = _make_result(success=True)
    with (
        mock.patch.object(mgr, "_diff_oversized", return_value=None),
        mock.patch.object(mgr, "_has_new_commits", return_value=True),
        mock.patch.object(mgr, "_warn_cross_repo_impact"),
        mock.patch.object(mgr, "_maybe_create_pr", return_value=None),
    ):
        out = mgr.finalize(req, result)
    git.push_branch_force.assert_called_once_with(ws, "goal/fix-widget")
    git.push_branch.assert_not_called()
    assert out.branch_pushed is True
    assert out.pull_request_url is None


def test_finalize_push_failure_is_nonfatal(tmp_path):
    ws = _git_repo(tmp_path)
    git = _finalize_git_ok()
    git.squash_commits.return_value = False
    git.push_branch.side_effect = RuntimeError("network down")
    mgr = WorkspaceManager(git_client=git)
    req = _make_request(ws)
    result = _make_result(success=True)
    with (
        mock.patch.object(mgr, "_diff_oversized", return_value=None),
        mock.patch.object(mgr, "_has_new_commits", return_value=True),
    ):
        out = mgr.finalize(req, result)
    assert out is result
    assert out.branch_pushed is False


# ── _diff_oversized ──────────────────────────────────────────────────────────


def test_diff_oversized_within_bounds(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    mgr = WorkspaceManager(max_files=50, max_lines=2000)

    def fake_run(args, **kwargs):
        if args[:3] == ["git", "diff", "--cached"] and "--shortstat" in args:
            return _fake_completed(0, stdout="1 file changed, 3 insertions(+), 1 deletion(-)")
        if args[:3] == ["git", "diff", "--cached"] and "--name-only" in args:
            return _fake_completed(0, stdout="a.py\nb.py\n")
        return _fake_completed(0)

    with mock.patch.object(ws_mod.subprocess, "run", side_effect=fake_run):
        assert mgr._diff_oversized(ws) is None


def test_diff_oversized_exceeds_files(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    mgr = WorkspaceManager(max_files=1, max_lines=10000)

    def fake_run(args, **kwargs):
        if "--shortstat" in args:
            return _fake_completed(0, stdout="3 files changed, 2 insertions(+)")
        if "--name-only" in args:
            return _fake_completed(0, stdout="z.py\n a.py \nb.py\n")
        return _fake_completed(0)

    with mock.patch.object(ws_mod.subprocess, "run", side_effect=fake_run):
        out = mgr._diff_oversized(ws)
    assert out is not None
    n_files, n_lines, file_list = out
    assert n_files == 3
    assert n_lines == 2
    # sorted + stripped
    assert file_list == ["a.py", "b.py", "z.py"]


def test_diff_oversized_exceeds_lines(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    mgr = WorkspaceManager(max_files=100, max_lines=5)

    def fake_run(args, **kwargs):
        if "--shortstat" in args:
            return _fake_completed(0, stdout="1 file changed, 10 insertions(+), 4 deletions(-)")
        if "--name-only" in args:
            return _fake_completed(0, stdout="a.py\n")
        return _fake_completed(0)

    with mock.patch.object(ws_mod.subprocess, "run", side_effect=fake_run):
        out = mgr._diff_oversized(ws)
    assert out is not None
    assert out[1] == 14  # 10 + 4


def test_diff_oversized_calledprocesserror_returns_none(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    mgr = WorkspaceManager()

    def fake_run(args, **kwargs):
        if kwargs.get("check"):
            raise subprocess.CalledProcessError(1, args)
        return _fake_completed(0)

    with mock.patch.object(ws_mod.subprocess, "run", side_effect=fake_run):
        assert mgr._diff_oversized(ws) is None


# ── _has_new_commits ─────────────────────────────────────────────────────────


def test_has_new_commits_true(tmp_path):
    ws = tmp_path / "ws"
    mgr = WorkspaceManager()
    with mock.patch.object(ws_mod.subprocess, "run", return_value=_fake_completed(0, stdout="3\n")):
        assert mgr._has_new_commits(ws, "main") is True


def test_has_new_commits_zero(tmp_path):
    ws = tmp_path / "ws"
    mgr = WorkspaceManager()
    with mock.patch.object(ws_mod.subprocess, "run", return_value=_fake_completed(0, stdout="0\n")):
        assert mgr._has_new_commits(ws, "main") is False


def test_has_new_commits_nonzero_returncode(tmp_path):
    ws = tmp_path / "ws"
    mgr = WorkspaceManager()
    with mock.patch.object(ws_mod.subprocess, "run", return_value=_fake_completed(1)):
        assert mgr._has_new_commits(ws, "main") is False


def test_has_new_commits_unparseable(tmp_path):
    ws = tmp_path / "ws"
    mgr = WorkspaceManager()
    with mock.patch.object(
        ws_mod.subprocess, "run", return_value=_fake_completed(0, stdout="not-a-number")
    ):
        assert mgr._has_new_commits(ws, "main") is False


# ── _commit_message ──────────────────────────────────────────────────────────


def test_commit_message_strips_noise(tmp_path):
    mgr = WorkspaceManager()
    req = _make_request(
        tmp_path / "ws", goal_text="[Impl] Add **bold** and `code` to the parser.\nmore"
    )
    assert mgr._commit_message(req) == "Add bold and code to the parser"


def test_commit_message_fallback_to_run_id(tmp_path):
    mgr = WorkspaceManager()
    req = _make_request(tmp_path / "ws", goal_text="", run_id="abcdef1234567890")
    assert mgr._commit_message(req) == "Operations Center run abcdef12"


def test_commit_message_fallback_when_only_noise(tmp_path):
    mgr = WorkspaceManager()
    req = _make_request(tmp_path / "ws", goal_text="[Tag]   ", run_id="zzzzzzzz1234")
    msg = mgr._commit_message(req)
    assert msg == "Operations Center run zzzzzzzz"


def test_commit_message_truncates_to_72(tmp_path):
    mgr = WorkspaceManager()
    long = "x" * 100
    req = _make_request(tmp_path / "ws", goal_text=long)
    assert mgr._commit_message(req) == "x" * 72


def test_commit_message_strips_heading_marker(tmp_path):
    mgr = WorkspaceManager()
    req = _make_request(tmp_path / "ws", goal_text="# Spec: queue-drain-20260602\nmore lines")
    assert mgr._commit_message(req) == "Spec: queue-drain-20260602"


def test_commit_message_strips_multi_hash_heading(tmp_path):
    mgr = WorkspaceManager()
    req = _make_request(tmp_path / "ws", goal_text="## Some heading\ndetails")
    assert mgr._commit_message(req) == "Some heading"


# ── _maybe_create_pr ─────────────────────────────────────────────────────────


def test_maybe_create_pr_no_token(tmp_path):
    mgr = WorkspaceManager()
    assert mgr._maybe_create_pr(_make_request(tmp_path / "ws")) is None


def test_maybe_create_pr_repo_not_in_await_review(tmp_path):
    mgr = WorkspaceManager(github_token="tok", await_review_repos={"other/repo"})
    assert mgr._maybe_create_pr(_make_request(tmp_path / "ws")) is None


def test_maybe_create_pr_open_pr_default_false(tmp_path):
    cfg = SimpleNamespace(open_pr_default=False)
    mgr = WorkspaceManager(
        github_token="tok",
        await_review_repos={"acme/widget"},
        repo_settings_lookup=lambda k: cfg,
    )
    assert mgr._maybe_create_pr(_make_request(tmp_path / "ws")) is None


def test_maybe_create_pr_success(tmp_path):
    mgr = WorkspaceManager(
        github_token="tok",
        await_review_repos={"acme/widget"},
        repo_settings_lookup=lambda k: SimpleNamespace(open_pr_default=True),
    )
    req = _make_request(tmp_path / "ws")

    fake_gh = mock.Mock()
    fake_gh.create_pr.return_value = {"html_url": "http://pr/99"}
    fake_cls = mock.Mock(return_value=fake_gh)
    fake_cls.owner_repo_from_clone_url.return_value = ("acme", "widget")
    fake_module = SimpleNamespace(GitHubPRClient=fake_cls)

    with mock.patch.dict("sys.modules", {"operations_center.adapters.github_pr": fake_module}):
        url = mgr._maybe_create_pr(req)
    assert url == "http://pr/99"
    fake_gh.create_pr.assert_called_once()
    _, kwargs = fake_gh.create_pr.call_args
    assert kwargs["head"] == "goal/fix-widget"
    assert kwargs["base"] == "main"


def test_maybe_create_pr_exception_returns_none(tmp_path):
    mgr = WorkspaceManager(
        github_token="tok",
        await_review_repos={"acme/widget"},
    )
    req = _make_request(tmp_path / "ws")

    fake_cls = mock.Mock()
    fake_cls.owner_repo_from_clone_url.side_effect = RuntimeError("bad url")
    fake_module = SimpleNamespace(GitHubPRClient=fake_cls)
    with mock.patch.dict("sys.modules", {"operations_center.adapters.github_pr": fake_module}):
        assert mgr._maybe_create_pr(req) is None


# ── _run_baseline_validation ─────────────────────────────────────────────────


def test_baseline_validation_no_repo_cfg_is_noop(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    mgr = WorkspaceManager(repo_settings_lookup=lambda k: None)
    mgr._run_baseline_validation(ws, _make_request(ws))
    assert not (ws / ".baseline-validation.json").exists()


def test_baseline_validation_writes_marker(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    cfg = SimpleNamespace()
    mgr = WorkspaceManager(repo_settings_lookup=lambda k: cfg)
    req = _make_request(ws)

    summary = mock.Mock()
    summary.model_dump_json.return_value = '{"status": "passed"}'
    fake_module = SimpleNamespace(run_baseline_validation=mock.Mock(return_value=summary))
    with mock.patch.dict(
        "sys.modules",
        {"operations_center.execution.baseline_validation": fake_module},
    ):
        mgr._run_baseline_validation(ws, req)
    assert (ws / ".baseline-validation.json").read_text(encoding="utf-8") == '{"status": "passed"}'


def test_baseline_validation_crash_is_nonfatal(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    cfg = SimpleNamespace()
    mgr = WorkspaceManager(repo_settings_lookup=lambda k: cfg)
    fake_module = SimpleNamespace(
        run_baseline_validation=mock.Mock(side_effect=RuntimeError("boom"))
    )
    with mock.patch.dict(
        "sys.modules",
        {"operations_center.execution.baseline_validation": fake_module},
    ):
        mgr._run_baseline_validation(ws, _make_request(ws))
    assert not (ws / ".baseline-validation.json").exists()


def test_baseline_validation_marker_write_oserror_is_nonfatal(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    mgr = WorkspaceManager(repo_settings_lookup=lambda k: SimpleNamespace())
    summary = mock.Mock()
    summary.model_dump_json.return_value = "{}"
    fake_module = SimpleNamespace(run_baseline_validation=mock.Mock(return_value=summary))
    with (
        mock.patch.dict(
            "sys.modules",
            {"operations_center.execution.baseline_validation": fake_module},
        ),
        mock.patch.object(Path, "write_text", side_effect=OSError("nope")) as write_text,
    ):
        mgr._run_baseline_validation(ws, _make_request(ws))  # no raise
    # the write was attempted but failed; marker must not exist
    write_text.assert_called_once()
    assert not (ws / ".baseline-validation.json").exists()


# ── _maybe_bootstrap ─────────────────────────────────────────────────────────


def test_bootstrap_no_repo_cfg_noop(tmp_path):
    ws = tmp_path / "ws"
    mgr = WorkspaceManager(repo_settings_lookup=lambda k: None)
    with mock.patch.object(ws_mod.subprocess, "run") as run:
        mgr._maybe_bootstrap(ws, _make_request(ws))
    run.assert_not_called()


def test_bootstrap_disabled_noop(tmp_path):
    ws = tmp_path / "ws"
    cfg = SimpleNamespace(bootstrap_enabled=False)
    mgr = WorkspaceManager(repo_settings_lookup=lambda k: cfg)
    with mock.patch.object(ws_mod.subprocess, "run") as run:
        mgr._maybe_bootstrap(ws, _make_request(ws))
    run.assert_not_called()


def test_bootstrap_nothing_configured_noop(tmp_path):
    ws = tmp_path / "ws"
    cfg = SimpleNamespace(bootstrap_commands=None, install_dev_command=None)
    mgr = WorkspaceManager(repo_settings_lookup=lambda k: cfg)
    with mock.patch.object(ws_mod.subprocess, "run") as run:
        mgr._maybe_bootstrap(ws, _make_request(ws))
    run.assert_not_called()


def test_bootstrap_custom_commands_success(tmp_path):
    ws = tmp_path / "ws"
    cfg = SimpleNamespace(
        bootstrap_commands=["echo hi", "  ", "", 123, "make setup"],
        install_dev_command="pip install -e .",
    )
    mgr = WorkspaceManager(repo_settings_lookup=lambda k: cfg)
    with mock.patch.object(ws_mod.subprocess, "run", return_value=_fake_completed(0)) as run:
        mgr._maybe_bootstrap(ws, _make_request(ws))
    # only the two valid string commands run; install path NOT reached
    ran = [c.args[0] for c in run.call_args_list]
    assert ran == ["echo hi", "make setup"]


def test_bootstrap_custom_command_failure_aborts(tmp_path):
    ws = tmp_path / "ws"
    cfg = SimpleNamespace(bootstrap_commands=["bad", "second"], install_dev_command=None)
    mgr = WorkspaceManager(repo_settings_lookup=lambda k: cfg)
    with mock.patch.object(
        ws_mod.subprocess, "run", return_value=_fake_completed(1, stderr="failed")
    ) as run:
        mgr._maybe_bootstrap(ws, _make_request(ws))
    # stops after first failing command
    assert run.call_count == 1


def test_bootstrap_standard_install_success(tmp_path):
    ws = tmp_path / "ws"
    cfg = SimpleNamespace(
        bootstrap_commands=None,
        install_dev_command="pip install -e .",
        venv_dir=".venv",
        python_binary="python3",
    )
    mgr = WorkspaceManager(repo_settings_lookup=lambda k: cfg)
    with mock.patch.object(ws_mod.subprocess, "run", return_value=_fake_completed(0)) as run:
        mgr._maybe_bootstrap(ws, _make_request(ws))
    # venv creation + install
    assert run.call_count == 2
    venv_call = run.call_args_list[0]
    assert venv_call.args[0] == ["python3", "-m", "venv", ".venv"]
    install_call = run.call_args_list[1]
    assert install_call.args[0] == "pip install -e ."


def test_bootstrap_defaults_venv_and_python(tmp_path):
    ws = tmp_path / "ws"
    cfg = SimpleNamespace(
        bootstrap_commands=None,
        install_dev_command="pip install -e .",
        venv_dir=None,
        python_binary=None,
    )
    mgr = WorkspaceManager(repo_settings_lookup=lambda k: cfg)
    with mock.patch.object(ws_mod.subprocess, "run", return_value=_fake_completed(0)) as run:
        mgr._maybe_bootstrap(ws, _make_request(ws))
    assert run.call_args_list[0].args[0] == ["python3", "-m", "venv", ".venv"]


def test_bootstrap_venv_creation_failure_aborts(tmp_path):
    ws = tmp_path / "ws"
    cfg = SimpleNamespace(
        bootstrap_commands=None,
        install_dev_command="pip install -e .",
        venv_dir=".venv",
        python_binary="python3",
    )
    mgr = WorkspaceManager(repo_settings_lookup=lambda k: cfg)
    with mock.patch.object(
        ws_mod.subprocess,
        "run",
        side_effect=subprocess.CalledProcessError(1, "venv"),
    ) as run:
        mgr._maybe_bootstrap(ws, _make_request(ws))
    assert run.call_count == 1  # only venv attempt


def test_bootstrap_install_command_failure_logged(tmp_path):
    ws = tmp_path / "ws"
    cfg = SimpleNamespace(
        bootstrap_commands=None,
        install_dev_command="pip install -e .",
        venv_dir=".venv",
        python_binary="python3",
    )
    mgr = WorkspaceManager(repo_settings_lookup=lambda k: cfg)

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if kwargs.get("check"):
            return _fake_completed(0)  # venv ok
        return _fake_completed(1, stderr="install failed")  # install fails

    with mock.patch.object(ws_mod.subprocess, "run", side_effect=fake_run):
        mgr._maybe_bootstrap(ws, _make_request(ws))
    assert len(calls) == 2


# ── _warn_cross_repo_impact ──────────────────────────────────────────────────


def test_warn_cross_repo_impact_helper_missing(tmp_path):
    ws = tmp_path / "ws"
    mgr = WorkspaceManager()
    with (
        mock.patch.dict("sys.modules", {"operations_center.cross_repo_impact": None}),
        mock.patch.object(ws_mod.subprocess, "run") as run,
    ):
        # importing a None module raises ImportError -> caught before any git work
        mgr._warn_cross_repo_impact(ws, _make_request(ws))  # no raise
    run.assert_not_called()


def test_warn_cross_repo_impact_no_settings_obj(tmp_path):
    ws = tmp_path / "ws"
    # plain lambda has no __self__ -> all_repos stays empty -> early return
    mgr = WorkspaceManager(repo_settings_lookup=lambda k: None)
    fake_module = SimpleNamespace(_check_cross_repo_impact=mock.Mock())
    with mock.patch.dict("sys.modules", {"operations_center.cross_repo_impact": fake_module}):
        mgr._warn_cross_repo_impact(ws, _make_request(ws))
    fake_module._check_cross_repo_impact.assert_not_called()


def test_warn_cross_repo_impact_logs_impacts(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()

    # bound method whose __self__ exposes .repos
    settings_obj = SimpleNamespace(repos={"acme/widget": object(), "other/repo": object()})

    class Lookup:
        def __init__(self, s):
            self.__self__ = s

        def __call__(self, k):
            return None

    # Build a real bound method so hasattr(__self__) is true
    def lookup(k):
        return None

    lookup.__self__ = settings_obj  # type: ignore[attr-defined]

    mgr = WorkspaceManager(repo_settings_lookup=lookup)
    req = _make_request(ws)

    impact = SimpleNamespace(repo_key="other/repo", matched_paths=["api/x.py"])
    check = mock.Mock(return_value=[impact])
    fake_module = SimpleNamespace(_check_cross_repo_impact=check)

    with (
        mock.patch.dict("sys.modules", {"operations_center.cross_repo_impact": fake_module}),
        mock.patch.object(
            ws_mod.subprocess,
            "run",
            return_value=_fake_completed(0, stdout="api/x.py\n\n"),
        ),
    ):
        mgr._warn_cross_repo_impact(ws, req)
    check.assert_called_once()
    _, kwargs = check.call_args
    assert kwargs["source_repo_key"] == "acme/widget"


def test_warn_cross_repo_impact_diff_fails(tmp_path):
    ws = tmp_path / "ws"
    settings_obj = SimpleNamespace(repos={"acme/widget": object()})

    def lookup(k):
        return None

    lookup.__self__ = settings_obj  # type: ignore[attr-defined]
    mgr = WorkspaceManager(repo_settings_lookup=lookup)
    check = mock.Mock()
    fake_module = SimpleNamespace(_check_cross_repo_impact=check)
    with (
        mock.patch.dict("sys.modules", {"operations_center.cross_repo_impact": fake_module}),
        mock.patch.object(
            ws_mod.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, "diff"),
        ),
    ):
        mgr._warn_cross_repo_impact(ws, _make_request(ws))
    check.assert_not_called()


def test_warn_cross_repo_impact_no_files(tmp_path):
    ws = tmp_path / "ws"
    settings_obj = SimpleNamespace(repos={"acme/widget": object()})

    def lookup(k):
        return None

    lookup.__self__ = settings_obj  # type: ignore[attr-defined]
    mgr = WorkspaceManager(repo_settings_lookup=lookup)
    check = mock.Mock()
    fake_module = SimpleNamespace(_check_cross_repo_impact=check)
    with (
        mock.patch.dict("sys.modules", {"operations_center.cross_repo_impact": fake_module}),
        mock.patch.object(ws_mod.subprocess, "run", return_value=_fake_completed(0, stdout="\n\n")),
    ):
        mgr._warn_cross_repo_impact(ws, _make_request(ws))
    check.assert_not_called()


# ── finalize() wires _warn before _maybe_create_pr (integration-ish) ─────────


def test_finalize_full_chain_with_validation_summary(tmp_path):
    ws = _git_repo(tmp_path)
    git = _finalize_git_ok()
    git.squash_commits.return_value = True
    mgr = WorkspaceManager(git_client=git)
    req = _make_request(ws)
    from operations_center.contracts.common import ValidationSummary

    result = _make_result(
        success=True, validation=ValidationSummary(status=ValidationStatus.PASSED)
    )
    with (
        mock.patch.object(mgr, "_diff_oversized", return_value=None),
        mock.patch.object(mgr, "_has_new_commits", return_value=True),
        mock.patch.object(mgr, "_warn_cross_repo_impact"),
        mock.patch.object(mgr, "_maybe_create_pr", return_value="http://pr/7"),
    ):
        out = mgr.finalize(req, result)
    assert out.pull_request_url == "http://pr/7"
    assert out.validation.status == ValidationStatus.PASSED
