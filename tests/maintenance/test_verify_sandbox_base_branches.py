# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for the sandbox base-branch preflight."""

from __future__ import annotations

from types import SimpleNamespace

from operations_center.entrypoints.maintenance import verify_sandbox_base_branches as mod
from operations_center.entrypoints.maintenance.verify_sandbox_base_branches import (
    SandboxBranchResult,
    main,
    scan,
)


class _FakeGit:
    """Duck-typed GitClient: tracks which remote branches 'exist' and creations."""

    def __init__(self, existing=(), verify_raises=None):
        self.existing = set(existing)
        self.created: list[tuple[str, str]] = []
        self._verify_raises = verify_raises

    def verify_remote_branch_exists(self, repo_path, branch):
        if self._verify_raises is not None:
            raise self._verify_raises
        if branch not in self.existing:
            raise ValueError(f"Base branch does not exist on remote: {branch}")

    def create_remote_branch_from(self, repo_path, branch, source_ref):
        self.created.append((branch, source_ref))
        self.existing.add(branch)


def _repo(tmp_path, *, sandbox="sandbox", default="main", git=True):
    p = tmp_path
    if git:
        (p / ".git").mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        local_path=str(p), sandbox_base_branch=sandbox, default_branch=default
    )


def _settings(repos):
    return SimpleNamespace(repos=repos)


def _noop_fetch(_path):
    return None


def test_existing_branch_is_present_not_missing(tmp_path):
    s = _settings({"R": _repo(tmp_path, sandbox="sandbox")})
    res = scan(s, git=_FakeGit(existing=["sandbox"]), fetch=_noop_fetch)
    assert len(res) == 1
    assert isinstance(res[0], SandboxBranchResult)
    assert res[0].exists is True
    assert res[0].missing is False


def test_missing_branch_without_heal_is_flagged(tmp_path):
    s = _settings({"R": _repo(tmp_path, sandbox="sandbox")})
    res = scan(s, git=_FakeGit(existing=[]), fetch=_noop_fetch)
    assert res[0].exists is False
    assert res[0].healed is False
    assert res[0].missing is True


def test_missing_branch_with_heal_is_created(tmp_path):
    git = _FakeGit(existing=[])
    s = _settings({"R": _repo(tmp_path, sandbox="sandbox", default="main")})
    res = scan(s, heal=True, git=git, fetch=_noop_fetch)
    assert res[0].healed is True
    assert res[0].exists is True
    assert res[0].missing is False
    assert git.created == [("sandbox", "origin/main")]


def test_no_sandbox_configured_is_skipped(tmp_path):
    s = _settings({"R": _repo(tmp_path, sandbox=None)})
    res = scan(s, git=_FakeGit(), fetch=_noop_fetch)
    assert res[0].skipped is True
    assert res[0].missing is False


def test_no_local_checkout_is_skipped_with_error(tmp_path):
    s = _settings({"R": _repo(tmp_path, sandbox="sandbox", git=False)})
    res = scan(s, git=_FakeGit(), fetch=_noop_fetch)
    assert res[0].skipped is True
    assert res[0].error == "no local checkout"
    assert res[0].missing is False


def test_ls_remote_failure_is_error_not_missing(tmp_path):
    s = _settings({"R": _repo(tmp_path, sandbox="sandbox")})
    res = scan(s, git=_FakeGit(verify_raises=RuntimeError("network down")), fetch=_noop_fetch)
    assert res[0].error is not None and "ls-remote failed" in res[0].error
    assert res[0].missing is False  # transport failure ≠ a known-missing branch


def test_heal_failure_records_error(tmp_path):
    class _BoomGit(_FakeGit):
        def create_remote_branch_from(self, repo_path, branch, source_ref):
            raise RuntimeError("push denied")

    s = _settings({"R": _repo(tmp_path, sandbox="sandbox")})
    res = scan(s, heal=True, git=_BoomGit(existing=[]), fetch=_noop_fetch)
    assert res[0].healed is False
    assert "heal failed" in (res[0].error or "")


def test_repo_without_local_path_is_dropped(tmp_path):
    s = _settings({"R": SimpleNamespace(local_path=None, sandbox_base_branch="x", default_branch="main")})
    res = scan(s, git=_FakeGit(), fetch=_noop_fetch)
    assert res == []  # no local checkout configured → not serviced here


def test_main_exit_1_when_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mod, "load_settings", lambda _c: _settings({"R": _repo(tmp_path, sandbox="sandbox")}))
    monkeypatch.setattr(mod, "scan", lambda *a, **k: [SandboxBranchResult("R", "sandbox", exists=False)])
    rc = main(["--config", "x.yaml"])
    assert rc == 1
    assert "MISSING" in capsys.readouterr().out


def test_main_exit_0_when_all_present(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mod, "load_settings", lambda _c: _settings({"R": _repo(tmp_path, sandbox="sandbox")}))
    monkeypatch.setattr(mod, "scan", lambda *a, **k: [SandboxBranchResult("R", "sandbox", exists=True)])
    rc = main(["--config", "x.yaml"])
    assert rc == 0
    assert "OK" in capsys.readouterr().out


def test_main_json_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mod, "load_settings", lambda _c: _settings({"R": _repo(tmp_path, sandbox="sandbox")}))
    monkeypatch.setattr(mod, "scan", lambda *a, **k: [SandboxBranchResult("R", "sandbox", exists=True, healed=True)])
    rc = main(["--config", "x.yaml", "--json", "--heal"])
    import json as _json

    payload = _json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["results"][0]["healed"] is True
