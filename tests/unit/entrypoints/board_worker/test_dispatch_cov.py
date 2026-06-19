# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from operations_center.entrypoints.board_worker import dispatch


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_settings(repos=None):
    return SimpleNamespace(
        repos=repos or {},
        plane=SimpleNamespace(project_id="proj-1"),
        team_executor=SimpleNamespace(timeout_seconds=900),
    )


def _make_repo_cfg(**kw):
    defaults = dict(
        clone_url="https://example.com/repo.git",
        sandbox_base_branch=None,
        default_branch="main",
        local_path=None,
        require_explicit_approval=False,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _make_issue(
    task_id="abcdef0123456789",
    labels=None,
    name="My Task",
    description="do the thing",
):
    return {
        "id": task_id,
        "labels": labels if labels is not None else [{"name": "repo: myrepo"}],
        "name": name,
        "description_stripped": description,
    }


class FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _install_common_patches(monkeypatch):
    """Patch out subprocess-adjacent helpers so dispatch is hermetic."""
    monkeypatch.setattr(dispatch, "venv_python", lambda root: "/fake/python")
    monkeypatch.setattr(dispatch, "build_allowlist_env", lambda root, **kw: {"PATH": "/fake"})
    monkeypatch.setattr(dispatch, "task_type_from_kind", lambda kind: "feature")
    monkeypatch.setattr(dispatch, "desc_text", lambda issue: issue.get("description_stripped", ""))
    monkeypatch.setattr(dispatch, "extract_goal", lambda desc, title: desc or title)


# ── _repo_local_path ───────────────────────────────────────────────────────────


def test_repo_local_path_uses_configured_local_path():
    settings = _make_settings({"r": _make_repo_cfg(local_path="/srv/r")})
    assert dispatch._repo_local_path(settings, "r") == "/srv/r"


def test_repo_local_path_falls_back_to_github_dir():
    settings = _make_settings({})
    out = dispatch._repo_local_path(settings, "ghost")
    assert out.endswith("/ghost")
    assert str(dispatch.GITHUB_DIR) in out


def test_repo_local_path_repo_without_local_path():
    settings = _make_settings({"r": _make_repo_cfg(local_path=None)})
    out = dispatch._repo_local_path(settings, "r")
    assert out.endswith("/r")


# ── prompt builders ──────────────────────────────────────────────────────────


def test_append_definition_of_done():
    out = dispatch._append_definition_of_done("GOAL")
    assert out.startswith("GOAL")
    assert "Definition of done" in out


def test_append_improve_output_prompt():
    out = dispatch._append_improve_output_prompt("GOAL")
    assert out.startswith("GOAL")
    assert "improve-output.json" in out


# ── _build_forwarded_labels ──────────────────────────────────────────────────


def test_build_forwarded_labels_no_explicit_required():
    labels = [
        {"name": "review_required"},
        {"name": "source: autonomy"},
        {"name": "source: human"},
        {"name": "repo: x"},
        "plain-string",
    ]
    out = dispatch._build_forwarded_labels(labels, _make_repo_cfg(require_explicit_approval=False))
    assert "review_required" in out
    assert "source: autonomy" in out
    assert "source: human" in out
    # Non-source / non-review labels are dropped.
    assert "repo: x" not in out
    assert "plain-string" not in out


def test_build_forwarded_labels_explicit_required_filters_and_appends():
    labels = [
        {"name": "source: autonomy"},
        {"name": "source: human"},
    ]
    out = dispatch._build_forwarded_labels(labels, _make_repo_cfg(require_explicit_approval=True))
    assert "source: autonomy" not in out  # filtered out
    assert "source: human" in out  # kept
    assert "review_required" in out  # appended


def test_build_forwarded_labels_none_repo_cfg():
    out = dispatch._build_forwarded_labels([{"name": "source: x"}], None)
    assert out == ["source: x"]


# ── dispatch_issue: spec-author short-circuit ────────────────────────────────


def test_dispatch_issue_spec_author_short_circuits(monkeypatch):
    _install_common_patches(monkeypatch)
    sentinel = MagicMock(return_value=True)
    monkeypatch.setattr(dispatch, "_dispatch_spec_author", sentinel)
    issue = _make_issue(labels=[{"name": "task-kind: spec-author"}, {"name": "repo: r"}])
    out = dispatch.dispatch_issue(issue, "goal", "/cfg.yaml", _make_settings(), MagicMock())
    assert out is True
    assert sentinel.called


# ── dispatch_issue: planning failures ────────────────────────────────────────


def _planning_setup(monkeypatch, plan_proc, settings=None):
    _install_common_patches(monkeypatch)
    settings = settings or _make_settings({"myrepo": _make_repo_cfg()})
    fail_task = MagicMock()
    monkeypatch.setattr(dispatch, "fail_task", fail_task)
    monkeypatch.setattr(dispatch.subprocess, "run", MagicMock(return_value=plan_proc))
    return settings, fail_task


def test_dispatch_issue_planning_no_json(monkeypatch):
    settings, fail_task = _planning_setup(
        monkeypatch, FakeProc(stdout="not json", stderr="err", returncode=0)
    )
    client = MagicMock()
    out = dispatch.dispatch_issue(_make_issue(), "goal", "/cfg.yaml", settings, client)
    assert out is False
    assert "no JSON" in fail_task.call_args[0][3]


def test_dispatch_issue_planning_nonzero_returncode(monkeypatch):
    bundle = {"message": "kaboom"}
    settings, fail_task = _planning_setup(
        monkeypatch, FakeProc(stdout=json.dumps(bundle), returncode=2)
    )
    client = MagicMock()
    out = dispatch.dispatch_issue(_make_issue(), "improve", "/cfg.yaml", settings, client)
    assert out is False
    assert "planning failed: kaboom" in fail_task.call_args[0][3]


def test_dispatch_issue_rejection_patterns_appended(monkeypatch):
    _install_common_patches(monkeypatch)
    settings = _make_settings({"myrepo": _make_repo_cfg()})
    monkeypatch.setattr(dispatch, "fail_task", MagicMock())

    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return FakeProc(stdout="notjson", returncode=0)

    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)

    # Inject a fake quality_alerts module so the import inside dispatch succeeds.
    import sys

    fake_mod = SimpleNamespace(
        _load_rejection_patterns_for_proposal=lambda repo_key: ["bad1", "bad2"]
    )
    monkeypatch.setitem(sys.modules, "operations_center.quality_alerts", fake_mod)

    dispatch.dispatch_issue(_make_issue(), "goal", "/cfg.yaml", settings, MagicMock())
    goal_idx = captured["cmd"].index("--goal") + 1
    assert "Rejection patterns to avoid" in captured["cmd"][goal_idx]
    assert "bad1" in captured["cmd"][goal_idx]


# ── dispatch_issue: full execution path ──────────────────────────────────────


def _run_dispatch_execution(
    monkeypatch,
    tmp_path,
    *,
    role="goal",
    plan_bundle=None,
    exec_outcome=None,
    write_result=True,
    result_text=None,
    retry_outcome=None,
    settings=None,
    labels=None,
    workspace_files=None,
):
    """Drive dispatch_issue through the execute branch.

    Returns (out, handle_success_mock, handle_failure_mock, fail_task_mock, client).
    """
    _install_common_patches(monkeypatch)
    settings = settings or _make_settings({"myrepo": _make_repo_cfg()})
    plan_bundle = plan_bundle if plan_bundle is not None else {"proposal": {}}

    hs = MagicMock()
    hf = MagicMock()
    ft = MagicMock()
    monkeypatch.setattr(dispatch, "handle_success", hs)
    monkeypatch.setattr(dispatch, "handle_failure", hf)
    monkeypatch.setattr(dispatch, "fail_task", ft)
    monkeypatch.setattr(dispatch, "add_label", MagicMock())
    monkeypatch.setattr(dispatch, "read_improve_output", lambda ws: [{"title": "s"}])
    monkeypatch.setattr(
        dispatch, "is_transient_failure", lambda r: r.get("failure_reason") == "network"
    )

    # Force tempfile to use a deterministic dir under tmp_path so we can inspect.
    tmpdir = tmp_path / "work"
    tmpdir.mkdir()

    class FakeTmp:
        def __init__(self, prefix=""):
            self.name = str(tmpdir)

        def __enter__(self):
            return self.name

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(dispatch.tempfile, "TemporaryDirectory", FakeTmp)
    monkeypatch.setattr(dispatch.shutil, "copy", lambda a, b: None)

    config_path = tmp_path / "cfg.yaml"
    config_path.write_text("x: 1")

    calls = {"n": 0}

    def fake_run(cmd, **kw):
        calls["n"] += 1
        # Planning call is first.
        if "operations_center.entrypoints.worker.main" in cmd:
            return FakeProc(stdout=json.dumps(plan_bundle), returncode=0)
        # Execute call(s): write result file at --output path.
        out_path = cmd[cmd.index("--output") + 1]
        is_retry = "_retry" in cmd[cmd.index("--source") + 1]
        ws_path = tmpdir / "workspace"
        if workspace_files:
            for fname, content in workspace_files.items():
                (ws_path / fname).write_text(content)
        if is_retry:
            if retry_outcome is not None:
                from pathlib import Path as _P

                _P(out_path).write_text(json.dumps(retry_outcome))
            return FakeProc(returncode=0)
        if write_result:
            from pathlib import Path as _P

            txt = result_text if result_text is not None else json.dumps(exec_outcome or {})
            _P(out_path).write_text(txt)
        return FakeProc(returncode=0)

    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)

    client = MagicMock()
    issue = _make_issue(labels=labels)
    out = dispatch.dispatch_issue(issue, role, config_path, settings, client)
    return out, hs, hf, ft, client


def test_dispatch_issue_execute_no_result_file(monkeypatch, tmp_path):
    out, hs, hf, ft, _ = _run_dispatch_execution(monkeypatch, tmp_path, write_result=False)
    assert out is False
    assert "no result file" in ft.call_args[0][3]


def test_dispatch_issue_execute_empty_result(monkeypatch, tmp_path):
    monkeypatch.setattr(dispatch, "add_label", MagicMock())
    inc = MagicMock()
    import operations_center.entrypoints.board_worker.labels as labels_mod

    monkeypatch.setattr(labels_mod, "increment_retry_count", inc)
    out, hs, hf, ft, client = _run_dispatch_execution(monkeypatch, tmp_path, result_text="   ")
    assert out is False
    assert inc.called
    assert "empty result.json" in ft.call_args[0][3]


def test_dispatch_issue_execute_success(monkeypatch, tmp_path):
    outcome = {
        "result": {
            "success": True,
            "status": "done",
            "needs_verification": False,
            "pull_request_url": "https://pr/1",
        }
    }
    out, hs, hf, ft, _ = _run_dispatch_execution(monkeypatch, tmp_path, exec_outcome=outcome)
    assert out is True
    assert hs.called
    assert hf.called is False


def test_dispatch_issue_execute_success_improve_role(monkeypatch, tmp_path):
    outcome = {"result": {"success": True, "status": "done"}}
    out, hs, hf, ft, _ = _run_dispatch_execution(
        monkeypatch, tmp_path, role="improve", exec_outcome=outcome
    )
    assert out is True
    # improve_suggestions forwarded.
    assert hs.call_args.kwargs["improve_suggestions"] == [{"title": "s"}]


def test_dispatch_issue_execute_failure(monkeypatch, tmp_path):
    outcome = {"result": {"success": False, "status": "errored"}}
    out, hs, hf, ft, _ = _run_dispatch_execution(monkeypatch, tmp_path, exec_outcome=outcome)
    assert out is False
    assert hf.called
    assert hs.called is False


def test_dispatch_issue_scope_too_wide(monkeypatch, tmp_path):
    outcome = {
        "result": {
            "success": True,
            "status": "done",
            "branch_pushed": False,
            "failure_category": "scope_too_wide",
        }
    }
    out, hs, hf, ft, _ = _run_dispatch_execution(
        monkeypatch,
        tmp_path,
        exec_outcome=outcome,
        workspace_files={"scope-too-wide.json": json.dumps({"files": ["a.py", "b.py"]})},
    )
    assert out is False
    assert hf.called
    assert hf.call_args.kwargs["scope_files"] == ["a.py", "b.py"]


def test_dispatch_issue_scope_file_bad_json(monkeypatch, tmp_path):
    outcome = {
        "result": {
            "success": True,
            "status": "done",
            "branch_pushed": False,
            "failure_category": "scope_too_wide",
        }
    }
    out, hs, hf, ft, _ = _run_dispatch_execution(
        monkeypatch,
        tmp_path,
        exec_outcome=outcome,
        workspace_files={"scope-too-wide.json": "{not json"},
    )
    assert out is False
    assert hf.call_args.kwargs["scope_files"] == []


def test_dispatch_issue_transient_retry_succeeds(monkeypatch, tmp_path):
    first = {"result": {"success": False, "status": "err", "failure_reason": "network"}}
    retry = {"result": {"success": True, "status": "done"}}
    out, hs, hf, ft, _ = _run_dispatch_execution(
        monkeypatch, tmp_path, exec_outcome=first, retry_outcome=retry
    )
    assert out is True
    assert hs.called


def test_dispatch_issue_transient_retry_no_file(monkeypatch, tmp_path):
    first = {"result": {"success": False, "status": "err", "failure_reason": "network"}}
    # retry_outcome None => retry subprocess writes nothing => keep original failure.
    out, hs, hf, ft, _ = _run_dispatch_execution(
        monkeypatch, tmp_path, exec_outcome=first, retry_outcome=None
    )
    assert out is False
    assert hf.called


# ── CI loop branch ───────────────────────────────────────────────────────────


def test_dispatch_issue_ci_loop_branch(monkeypatch, tmp_path):
    _install_common_patches(monkeypatch)
    settings = _make_settings({"myrepo": _make_repo_cfg()})
    monkeypatch.setattr(dispatch.shutil, "copy", lambda a, b: None)

    run_ci = MagicMock(return_value=True)
    monkeypatch.setattr(dispatch, "run_ci_loop", run_ci)

    tmpdir = tmp_path / "work"
    tmpdir.mkdir()

    class FakeTmp:
        def __init__(self, prefix=""):
            pass

        def __enter__(self):
            return str(tmpdir)

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(dispatch.tempfile, "TemporaryDirectory", FakeTmp)

    bundle = {"proposal": {"continuous_improvement": {"spec": 1}}}

    def fake_run(cmd, **kw):
        return FakeProc(stdout=json.dumps(bundle), returncode=0)

    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)

    config_path = tmp_path / "cfg.yaml"
    config_path.write_text("x: 1")

    labels = [{"name": "repo: myrepo"}, {"name": "task-kind: improve_campaign"}]
    out = dispatch.dispatch_issue(
        _make_issue(labels=labels), "improve", config_path, settings, MagicMock()
    )
    assert out is True
    assert run_ci.called


# ── _dispatch_spec_author ────────────────────────────────────────────────────


def test_dispatch_spec_author_no_payload(monkeypatch):
    _install_common_patches(monkeypatch)
    ft = MagicMock()
    monkeypatch.setattr(dispatch, "fail_task", ft)

    import operations_center.entrypoints.board_worker._text as text_mod

    monkeypatch.setattr(text_mod, "parse_spec_author_payload", lambda d: None)

    out = dispatch._dispatch_spec_author(
        issue=_make_issue(),
        role="goal",
        settings=_make_settings(),
        client=MagicMock(),
        config_path="/cfg.yaml",
        description="no yaml",
        labels=[],
        task_id="t1",
    )
    assert out is False
    assert "payload missing" in ft.call_args[0][3]


def test_dispatch_spec_author_happy(monkeypatch):
    _install_common_patches(monkeypatch)
    monkeypatch.setattr(dispatch, "venv_python", lambda r: "/py")
    monkeypatch.setattr(dispatch, "build_allowlist_env", lambda r, **kw: {})

    import operations_center.entrypoints.board_worker._text as text_mod

    payload = {
        "target_path": "specs/x.md",
        "spec_slug": "x",
        "trigger_source": "cron",
        "task_phase": "draft",
    }
    monkeypatch.setattr(text_mod, "parse_spec_author_payload", lambda d: payload)
    monkeypatch.setattr(text_mod, "build_spec_author_goal_text", lambda p, rid: "GOAL")

    proc = MagicMock(return_value=True)
    monkeypatch.setattr(dispatch, "process_spec_author", proc)

    repo_cfg = _make_repo_cfg(clone_url="git://spec", sandbox_base_branch="sbx")
    settings = _make_settings({dispatch.SPEC_AUTHOR_REPO_KEY: repo_cfg})

    out = dispatch._dispatch_spec_author(
        issue=_make_issue(),
        role="goal",
        settings=settings,
        client=MagicMock(),
        config_path="/cfg.yaml",
        description="yaml here",
        labels=[],
        task_id="abcdef0123",
    )
    assert out is True
    kwargs = proc.call_args.kwargs
    assert kwargs["clone_url"] == "git://spec"
    assert kwargs["base_branch"] == "sbx"
    assert kwargs["goal_text"] == "GOAL"
    assert kwargs["spec_slug"] == "x"


def test_dispatch_spec_author_no_repo_cfg_uses_file_url(monkeypatch):
    _install_common_patches(monkeypatch)
    monkeypatch.setattr(dispatch, "venv_python", lambda r: "/py")
    monkeypatch.setattr(dispatch, "build_allowlist_env", lambda r, **kw: {})

    import operations_center.entrypoints.board_worker._text as text_mod

    monkeypatch.setattr(
        text_mod, "parse_spec_author_payload", lambda d: {"target_path": "p", "spec_slug": "s"}
    )
    monkeypatch.setattr(text_mod, "build_spec_author_goal_text", lambda p, rid: "G")

    proc = MagicMock(return_value=False)
    monkeypatch.setattr(dispatch, "process_spec_author", proc)

    out = dispatch._dispatch_spec_author(
        issue=_make_issue(),
        role="goal",
        settings=_make_settings({}),
        client=MagicMock(),
        config_path="/cfg.yaml",
        description="d",
        labels=[],
        task_id="t1",
    )
    assert out is False
    assert proc.call_args.kwargs["clone_url"].startswith("file://")
    assert proc.call_args.kwargs["base_branch"] == "main"
