# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from operations_center.entrypoints.board_worker import spec_author
from operations_center.entrypoints.board_worker.labels import STATE_DONE


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_settings(repos=None, project_id="proj-1"):
    return SimpleNamespace(
        plane=SimpleNamespace(project_id=project_id),
        repos=repos or {},
    )


def _make_issue(issue_id="123", labels=None):
    return {"id": issue_id, "labels": labels or []}


def _make_proc(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _base_kwargs(**overrides):
    kw = dict(
        issue=_make_issue(),
        role="spec-author",
        settings=_make_settings(),
        client=MagicMock(),
        config_path=None,  # filled by tests that need a real file
        goal_text="write a spec",
        repo_key="OperationsCenter",
        clone_url="https://example/repo.git",
        base_branch="main",
        spec_slug="my-spec",
        target_path="docs/specs/my-spec.md",
        trigger_source="trigger-x",
        task_phase="",
        python="python3",
        oc_root=None,
        env={"FOO": "bar"},
        short_id="abc123",
    )
    kw.update(overrides)
    return kw


@pytest.fixture
def config_file(tmp_path):
    cfg = tmp_path / "ops.yaml"
    cfg.write_text("config: yes\n", encoding="utf-8")
    return cfg


# ── process_spec_author ───────────────────────────────────────────────────────


def test_planning_no_json_fails(monkeypatch, tmp_path, config_file):
    """plan_proc.stdout not JSON -> fail_task, return False."""
    fail = MagicMock()
    monkeypatch.setattr(spec_author, "fail_task", fail)
    proc = _make_proc(returncode=0, stdout="not json", stderr="boom")
    monkeypatch.setattr(spec_author.subprocess, "run", lambda *a, **k: proc)

    kw = _base_kwargs(config_path=config_file, oc_root=tmp_path)
    assert spec_author.process_spec_author(**kw) is False
    fail.assert_called_once()
    assert "no JSON" in fail.call_args.args[3]


def test_planning_nonzero_returncode_fails(monkeypatch, tmp_path, config_file):
    """Valid JSON but returncode != 0 -> fail with bundle message."""
    fail = MagicMock()
    monkeypatch.setattr(spec_author, "fail_task", fail)
    proc = _make_proc(returncode=2, stdout=json.dumps({"message": "bad plan"}))
    monkeypatch.setattr(spec_author.subprocess, "run", lambda *a, **k: proc)

    kw = _base_kwargs(config_path=config_file, oc_root=tmp_path)
    assert spec_author.process_spec_author(**kw) is False
    assert "bad plan" in fail.call_args.args[3]


def test_planning_nonzero_default_message(monkeypatch, tmp_path, config_file):
    """returncode != 0 with no message key -> 'unknown planning error'."""
    fail = MagicMock()
    monkeypatch.setattr(spec_author, "fail_task", fail)
    proc = _make_proc(returncode=1, stdout=json.dumps({}))
    monkeypatch.setattr(spec_author.subprocess, "run", lambda *a, **k: proc)

    kw = _base_kwargs(config_path=config_file, oc_root=tmp_path)
    assert spec_author.process_spec_author(**kw) is False
    assert "unknown planning error" in fail.call_args.args[3]


class _ProcSequencer:
    """Mimics subprocess.run for plan then execute, writing result file."""

    def __init__(self, plan_proc, *, write_result=None, result_path_key="--output"):
        self.plan_proc = plan_proc
        self.write_result = write_result
        self.result_path_key = result_path_key
        self.calls = 0
        self.cmds = []

    def __call__(self, cmd, *a, **k):
        self.cmds.append(cmd)
        self.calls += 1
        if self.calls == 1:
            return self.plan_proc
        # execute call: optionally write the result file
        if self.write_result is not None:
            idx = cmd.index(self.result_path_key)
            from pathlib import Path

            Path(cmd[idx + 1]).write_text(self.write_result, encoding="utf-8")
        return _make_proc(returncode=0, stdout="")


def test_execute_no_result_file_fails(monkeypatch, tmp_path, config_file):
    """Execute writes nothing -> result_file missing -> fail."""
    fail = MagicMock()
    monkeypatch.setattr(spec_author, "fail_task", fail)
    seq = _ProcSequencer(_make_proc(returncode=0, stdout=json.dumps({"ok": 1})), write_result=None)
    monkeypatch.setattr(spec_author.subprocess, "run", seq)

    kw = _base_kwargs(config_path=config_file, oc_root=tmp_path)
    assert spec_author.process_spec_author(**kw) is False
    assert "no result file" in fail.call_args.args[3]


def test_execute_result_bad_json_fails(monkeypatch, tmp_path, config_file):
    fail = MagicMock()
    monkeypatch.setattr(spec_author, "fail_task", fail)
    seq = _ProcSequencer(
        _make_proc(returncode=0, stdout=json.dumps({"ok": 1})), write_result="{ broken"
    )
    monkeypatch.setattr(spec_author.subprocess, "run", seq)

    kw = _base_kwargs(config_path=config_file, oc_root=tmp_path)
    assert spec_author.process_spec_author(**kw) is False
    assert "parse failed" in fail.call_args.args[3]


def test_execute_empty_result_treated_as_failure(monkeypatch, tmp_path, config_file):
    """Empty result file -> '{}' -> success False -> handle_failure path."""
    handle = MagicMock()
    monkeypatch.setattr(spec_author, "handle_failure", handle)
    seq = _ProcSequencer(_make_proc(returncode=0, stdout=json.dumps({"ok": 1})), write_result="")
    monkeypatch.setattr(spec_author.subprocess, "run", seq)

    kw = _base_kwargs(config_path=config_file, oc_root=tmp_path)
    assert spec_author.process_spec_author(**kw) is False
    handle.assert_called_once()


def test_execute_failure_calls_handle_failure(monkeypatch, tmp_path, config_file):
    handle = MagicMock()
    monkeypatch.setattr(spec_author, "handle_failure", handle)
    result = json.dumps({"result": {"success": False, "run_id": "r1"}})
    seq = _ProcSequencer(
        _make_proc(returncode=0, stdout=json.dumps({"ok": 1})), write_result=result
    )
    monkeypatch.setattr(spec_author.subprocess, "run", seq)

    kw = _base_kwargs(config_path=config_file, oc_root=tmp_path)
    assert spec_author.process_spec_author(**kw) is False
    handle.assert_called_once()


def test_success_invokes_handle_success(monkeypatch, tmp_path, config_file):
    success_handler = MagicMock()
    monkeypatch.setattr(spec_author, "handle_spec_author_success", success_handler)
    result = json.dumps({"result": {"success": True, "run_id": "run-99"}})
    seq = _ProcSequencer(
        _make_proc(returncode=0, stdout=json.dumps({"ok": 1})), write_result=result
    )
    monkeypatch.setattr(spec_author.subprocess, "run", seq)

    kw = _base_kwargs(config_path=config_file, oc_root=tmp_path)
    assert spec_author.process_spec_author(**kw) is True
    success_handler.assert_called_once()
    assert success_handler.call_args.kwargs["run_id"] == "run-99"


def test_forwarded_labels_and_cmd_shape(monkeypatch, tmp_path, config_file):
    """source: labels forwarded; plan cmd carries expected flags."""
    monkeypatch.setattr(spec_author, "handle_spec_author_success", MagicMock())
    result = json.dumps({"result": {"success": True, "run_id": "r"}})
    seq = _ProcSequencer(
        _make_proc(returncode=0, stdout=json.dumps({"ok": 1})), write_result=result
    )
    monkeypatch.setattr(spec_author.subprocess, "run", seq)

    issue = _make_issue(
        labels=[
            {"name": "source: github"},
            {"name": "other"},
            "source: plane",  # non-dict label form
            "ignore",
        ]
    )
    kw = _base_kwargs(config_path=config_file, oc_root=tmp_path, issue=issue)
    assert spec_author.process_spec_author(**kw) is True

    plan_cmd = seq.cmds[0]
    # both source: labels forwarded as --label
    label_args = [plan_cmd[i + 1] for i, v in enumerate(plan_cmd) if v == "--label"]
    assert "source: github" in label_args
    assert "source: plane" in label_args
    assert "other" not in label_args
    # spec-specific constraints present
    assert "--max-changed-files" in plan_cmd
    assert "docs/specs/" in plan_cmd
    assert str(spec_author.SPEC_AUTHOR_TIMEOUT_SECONDS) in plan_cmd
    # exec cmd carries the spec-author branch + source tag
    exec_cmd = seq.cmds[1]
    assert f"spec-author/{kw['short_id']}" in exec_cmd
    src_idx = exec_cmd.index("--source")
    assert exec_cmd[src_idx + 1].startswith("board_worker_spec_author|spec_slug=my-spec")


# ── handle_spec_author_success: phase-advance branch ──────────────────────────


def test_phase_advance_done(monkeypatch, tmp_path):
    monkeypatch.setattr(spec_author, "summarize_prompt_diff_block", lambda **k: (3, "parsed ok"))
    client = MagicMock()
    spec_author.handle_spec_author_success(
        client=client,
        issue=_make_issue("t9"),
        settings=_make_settings(),
        workspace=tmp_path,
        target_path="docs/specs/x.md",
        spec_slug="x",
        run_id="r1",
        task_phase="phase2",
    )
    client.transition_issue.assert_called_once_with("t9", STATE_DONE)
    assert "phase-advance" in client.comment_issue.call_args.args[1]


def test_phase_advance_edit_count_none(monkeypatch, tmp_path):
    """edit_count None branch in the log line."""
    monkeypatch.setattr(spec_author, "summarize_prompt_diff_block", lambda **k: (None, "no diff"))
    client = MagicMock()
    spec_author.handle_spec_author_success(
        client=client,
        issue=_make_issue("t9"),
        settings=_make_settings(),
        workspace=tmp_path,
        target_path="docs/specs/x.md",
        spec_slug="x",
        run_id="r1",
        task_phase="phaseX",
    )
    client.transition_issue.assert_called_once_with("t9", STATE_DONE)
    assert "phase-advance" in client.comment_issue.call_args.args[1]


def test_phase_advance_transition_exception_swallowed(monkeypatch, tmp_path):
    monkeypatch.setattr(spec_author, "summarize_prompt_diff_block", lambda **k: (1, "ok"))
    client = MagicMock()
    client.transition_issue.side_effect = RuntimeError("plane down")
    # should not raise even though the transition blew up
    spec_author.handle_spec_author_success(
        client=client,
        issue=_make_issue("t9"),
        settings=_make_settings(),
        workspace=tmp_path,
        target_path="docs/specs/x.md",
        spec_slug="x",
        run_id="r1",
        task_phase="p",
    )
    # transition was attempted; the raised error was swallowed
    client.transition_issue.assert_called_once_with("t9", STATE_DONE)


# ── handle_spec_author_success: spec-missing branch ───────────────────────────


def test_spec_missing_done_with_comment(tmp_path):
    client = MagicMock()
    spec_author.handle_spec_author_success(
        client=client,
        issue=_make_issue("m1"),
        settings=_make_settings(),
        workspace=tmp_path,
        target_path="docs/specs/missing.md",
        spec_slug="missing",
        run_id="r2",
        task_phase="",
    )
    client.transition_issue.assert_called_once_with("m1", STATE_DONE)
    assert "not found" in client.comment_issue.call_args.args[1]


def test_spec_missing_transition_exception_swallowed(tmp_path):
    client = MagicMock()
    client.transition_issue.side_effect = RuntimeError("x")
    # transition raising must be swallowed (no exception propagates)
    spec_author.handle_spec_author_success(
        client=client,
        issue=_make_issue("m1"),
        settings=_make_settings(),
        workspace=tmp_path,
        target_path="docs/specs/missing.md",
        spec_slug="missing",
        run_id="r2",
        task_phase="",
    )
    client.transition_issue.assert_called_once_with("m1", STATE_DONE)


# ── handle_spec_author_success: full campaign-build branch ────────────────────


def _install_fake_campaign(monkeypatch, *, created_ids, fm_repos=None, fm_raises=False):
    """Install fake operations_center.spec_author.* modules used lazily."""
    pkg = ModuleType("operations_center.spec_author")
    cb_mod = ModuleType("operations_center.spec_author.campaign_builder")
    models_mod = ModuleType("operations_center.spec_author.models")

    class FakeBuilder:
        def __init__(self, *, client, project_id):
            self.client = client
            self.project_id = project_id

        def build(self, *, spec_text, repo_key, base_branch):
            FakeBuilder.last = SimpleNamespace(
                spec_text=spec_text, repo_key=repo_key, base_branch=base_branch
            )
            return list(created_ids)

    class FakeFrontMatter:
        repos = fm_repos or []

        @classmethod
        def from_spec_text(cls, text):
            if fm_raises:
                raise ValueError("bad frontmatter")
            inst = cls()
            inst.repos = fm_repos or []
            return inst

    cb_mod.CampaignBuilder = FakeBuilder
    models_mod.SpecFrontMatter = FakeFrontMatter
    monkeypatch.setitem(sys.modules, "operations_center.spec_author", pkg)
    monkeypatch.setitem(sys.modules, "operations_center.spec_author.campaign_builder", cb_mod)
    monkeypatch.setitem(sys.modules, "operations_center.spec_author.models", models_mod)
    return FakeBuilder


def _write_spec(tmp_path, target_path="docs/specs/x.md", text="# spec\n"):
    p = tmp_path / target_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return target_path


def test_campaign_build_with_children_and_parent_labels(monkeypatch, tmp_path):
    target = _write_spec(tmp_path)
    builder = _install_fake_campaign(monkeypatch, created_ids=["c1", "c2"], fm_repos=["RepoA"])
    add_label = MagicMock()
    monkeypatch.setattr(spec_author, "add_label", add_label)

    repo_cfg = SimpleNamespace(sandbox_base_branch="sandbox", default_branch="main")
    settings = _make_settings(repos={"RepoA": repo_cfg})

    client = MagicMock()
    client.list_issues.return_value = [{"id": "c1"}, {"id": "c2"}, {"id": "other"}]

    spec_author.handle_spec_author_success(
        client=client,
        issue=_make_issue("p1"),
        settings=settings,
        workspace=tmp_path,
        target_path=target,
        spec_slug="x",
        run_id="run-7",
        task_phase="",
    )

    # repo_key resolved from frontmatter, base from sandbox_base_branch
    assert builder.last.repo_key == "RepoA"
    assert builder.last.base_branch == "sandbox"
    # parent_run labels added for each created child found in list_issues
    assert add_label.call_count == 2
    client.transition_issue.assert_called_once_with("p1", STATE_DONE)
    comment = client.comment_issue.call_args.args[1]
    assert "created 2 campaign task(s)" in comment
    assert "#c1" in comment and "#c2" in comment


def test_campaign_build_default_branch_when_no_sandbox(monkeypatch, tmp_path):
    target = _write_spec(tmp_path)
    builder = _install_fake_campaign(monkeypatch, created_ids=["c1"], fm_repos=["RepoB"])
    monkeypatch.setattr(spec_author, "add_label", MagicMock())
    repo_cfg = SimpleNamespace(sandbox_base_branch=None, default_branch="develop")
    settings = _make_settings(repos={"RepoB": repo_cfg})
    client = MagicMock()
    client.list_issues.return_value = [{"id": "c1"}]

    spec_author.handle_spec_author_success(
        client=client,
        issue=_make_issue("p2"),
        settings=settings,
        workspace=tmp_path,
        target_path=target,
        spec_slug="x",
        run_id="r",
        task_phase="",
    )
    assert builder.last.base_branch == "develop"


def test_campaign_build_no_repo_cfg_defaults_main(monkeypatch, tmp_path):
    target = _write_spec(tmp_path)
    builder = _install_fake_campaign(monkeypatch, created_ids=["c1"], fm_repos=[])
    monkeypatch.setattr(spec_author, "add_label", MagicMock())
    # repos empty -> repo_cfg None -> base_branch "main"; fm.repos empty -> SPEC_AUTHOR_REPO_KEY
    settings = _make_settings(repos={})
    client = MagicMock()
    client.list_issues.return_value = [{"id": "c1"}]

    spec_author.handle_spec_author_success(
        client=client,
        issue=_make_issue("p3"),
        settings=settings,
        workspace=tmp_path,
        target_path=target,
        spec_slug="x",
        run_id="r",
        task_phase="",
    )
    assert builder.last.repo_key == spec_author.SPEC_AUTHOR_REPO_KEY
    assert builder.last.base_branch == "main"


def test_campaign_build_frontmatter_raises_uses_default_repo(monkeypatch, tmp_path):
    target = _write_spec(tmp_path)
    builder = _install_fake_campaign(monkeypatch, created_ids=["c1"], fm_raises=True)
    monkeypatch.setattr(spec_author, "add_label", MagicMock())
    repo_cfg = SimpleNamespace(sandbox_base_branch=None, default_branch="main")
    settings = _make_settings(repos={spec_author.SPEC_AUTHOR_REPO_KEY: repo_cfg})
    client = MagicMock()
    client.list_issues.return_value = [{"id": "c1"}]

    spec_author.handle_spec_author_success(
        client=client,
        issue=_make_issue("p4"),
        settings=settings,
        workspace=tmp_path,
        target_path=target,
        spec_slug="x",
        run_id="r",
        task_phase="",
    )
    assert builder.last.repo_key == spec_author.SPEC_AUTHOR_REPO_KEY


def test_campaign_build_no_children_comment(monkeypatch, tmp_path):
    target = _write_spec(tmp_path)
    _install_fake_campaign(monkeypatch, created_ids=[], fm_repos=[])
    monkeypatch.setattr(spec_author, "add_label", MagicMock())
    settings = _make_settings(repos={})
    client = MagicMock()

    spec_author.handle_spec_author_success(
        client=client,
        issue=_make_issue("p5"),
        settings=settings,
        workspace=tmp_path,
        target_path=target,
        spec_slug="x",
        run_id="r",
        task_phase="",
    )
    # no children -> list_issues never consulted, "no children" comment
    client.list_issues.assert_not_called()
    assert "no children" in client.comment_issue.call_args.args[1]


def test_campaign_build_exception_then_done(monkeypatch, tmp_path):
    """CampaignBuilder.build raises -> created_ids stays [] -> still transitions Done."""
    target = _write_spec(tmp_path)
    pkg = ModuleType("operations_center.spec_author")
    cb_mod = ModuleType("operations_center.spec_author.campaign_builder")
    models_mod = ModuleType("operations_center.spec_author.models")

    class BoomBuilder:
        def __init__(self, **k):
            pass

        def build(self, **k):
            raise RuntimeError("build exploded")

    class FM:
        @classmethod
        def from_spec_text(cls, text):
            inst = cls()
            inst.repos = []
            return inst

    cb_mod.CampaignBuilder = BoomBuilder
    models_mod.SpecFrontMatter = FM
    monkeypatch.setitem(sys.modules, "operations_center.spec_author", pkg)
    monkeypatch.setitem(sys.modules, "operations_center.spec_author.campaign_builder", cb_mod)
    monkeypatch.setitem(sys.modules, "operations_center.spec_author.models", models_mod)

    client = MagicMock()
    spec_author.handle_spec_author_success(
        client=client,
        issue=_make_issue("p6"),
        settings=_make_settings(repos={}),
        workspace=tmp_path,
        target_path=target,
        spec_slug="x",
        run_id="r",
        task_phase="",
    )
    client.transition_issue.assert_called_once_with("p6", STATE_DONE)
    assert "no children" in client.comment_issue.call_args.args[1]


def test_parent_label_tagging_exception_swallowed(monkeypatch, tmp_path):
    target = _write_spec(tmp_path)
    _install_fake_campaign(monkeypatch, created_ids=["c1"], fm_repos=[])
    monkeypatch.setattr(spec_author, "add_label", MagicMock())
    settings = _make_settings(repos={})
    client = MagicMock()
    client.list_issues.side_effect = RuntimeError("list failed")

    # exception inside parent_run tagging must be swallowed; Done still happens
    spec_author.handle_spec_author_success(
        client=client,
        issue=_make_issue("p7"),
        settings=settings,
        workspace=tmp_path,
        target_path=target,
        spec_slug="x",
        run_id="run-z",
        task_phase="",
    )
    client.transition_issue.assert_called_once_with("p7", STATE_DONE)


def test_post_success_transition_exception_swallowed(monkeypatch, tmp_path):
    target = _write_spec(tmp_path)
    _install_fake_campaign(monkeypatch, created_ids=[], fm_repos=[])
    monkeypatch.setattr(spec_author, "add_label", MagicMock())
    settings = _make_settings(repos={})
    client = MagicMock()
    client.transition_issue.side_effect = RuntimeError("boom")
    # final try/except must swallow the transition error
    spec_author.handle_spec_author_success(
        client=client,
        issue=_make_issue("p8"),
        settings=settings,
        workspace=tmp_path,
        target_path=target,
        spec_slug="x",
        run_id="r",
        task_phase="",
    )
    client.transition_issue.assert_called_once_with("p8", STATE_DONE)


def test_spec_read_oserror_falls_back_to_empty(monkeypatch, tmp_path):
    """spec_path.exists() True but read raises OSError -> spec_text=''."""
    target = "docs/specs/x.md"
    p = tmp_path / target
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("data", encoding="utf-8")

    builder = _install_fake_campaign(monkeypatch, created_ids=[], fm_repos=[])
    monkeypatch.setattr(spec_author, "add_label", MagicMock())

    orig_read = spec_author.Path.read_text

    def fake_read(self, *a, **k):
        if str(self).endswith("x.md"):
            raise OSError("read denied")
        return orig_read(self, *a, **k)

    monkeypatch.setattr(spec_author.Path, "read_text", fake_read)

    client = MagicMock()
    spec_author.handle_spec_author_success(
        client=client,
        issue=_make_issue("p9"),
        settings=_make_settings(repos={}),
        workspace=tmp_path,
        target_path=target,
        spec_slug="x",
        run_id="r",
        task_phase="",
    )
    assert builder.last.spec_text == ""
    client.transition_issue.assert_called_once_with("p9", STATE_DONE)


def test_run_id_falsy_skips_parent_labels(monkeypatch, tmp_path):
    """created_ids present but run_id empty -> parent label block skipped."""
    target = _write_spec(tmp_path)
    _install_fake_campaign(monkeypatch, created_ids=["c1"], fm_repos=[])
    monkeypatch.setattr(spec_author, "add_label", MagicMock())
    client = MagicMock()

    spec_author.handle_spec_author_success(
        client=client,
        issue=_make_issue("p10"),
        settings=_make_settings(repos={}),
        workspace=tmp_path,
        target_path=target,
        spec_slug="x",
        run_id="",
        task_phase="",
    )
    client.list_issues.assert_not_called()
    client.transition_issue.assert_called_once_with("p10", STATE_DONE)
