# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from operations_center.adapters.git.client import GitClient, branch_allowed


def _proc(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# _run
# ---------------------------------------------------------------------------


def test_run_success_strips_output():
    client = GitClient()
    with patch("subprocess.run", return_value=_proc(stdout="  hello \n")) as sr:
        out = client._run(["git", "status"], cwd=Path("/repo"), timeout=12)
    assert out == "hello"
    sr.assert_called_once_with(
        ["git", "status"],
        cwd=Path("/repo"),
        capture_output=True,
        text=True,
        check=False,
        timeout=12,
    )


def test_run_failure_raises_with_stderr():
    client = GitClient()
    with patch("subprocess.run", return_value=_proc(returncode=1, stderr="boom")):
        with pytest.raises(RuntimeError) as exc:
            client._run(["git", "status"])
    assert "git command failed" in str(exc.value)
    assert "boom" in str(exc.value)


# ---------------------------------------------------------------------------
# _run_bytes
# ---------------------------------------------------------------------------


def test_run_bytes_success():
    client = GitClient()
    with patch("subprocess.run", return_value=_proc(stdout=b"raw")):
        out = client._run_bytes(["git", "x"], cwd=Path("/r"))
    assert out == b"raw"


def test_run_bytes_failure_decodes_stderr():
    client = GitClient()
    with patch("subprocess.run", return_value=_proc(returncode=2, stderr=b"\xff bad")):
        with pytest.raises(RuntimeError) as exc:
            client._run_bytes(["git", "x"])
    assert "git command failed" in str(exc.value)


# ---------------------------------------------------------------------------
# clone
# ---------------------------------------------------------------------------


def test_clone_returns_repo_path(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", return_value="") as run:
        result = client.clone("https://example/repo.git", tmp_path)
    assert result == tmp_path / "repo"
    run.assert_called_once_with(
        ["git", "clone", "https://example/repo.git", str(tmp_path / "repo")]
    )


# ---------------------------------------------------------------------------
# add_local_exclude
# ---------------------------------------------------------------------------


def test_add_local_exclude_creates_file(tmp_path):
    client = GitClient()
    repo = tmp_path / "repo"
    repo.mkdir()
    client.add_local_exclude(repo, "artifact.json")
    exclude = repo / ".git" / "info" / "exclude"
    assert exclude.read_text(encoding="utf-8") == "artifact.json\n"


def test_add_local_exclude_already_present_noop(tmp_path):
    client = GitClient()
    repo = tmp_path / "repo"
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True)
    exclude.write_text("foo\nbar\n", encoding="utf-8")
    client.add_local_exclude(repo, "  foo  ")
    assert exclude.read_text(encoding="utf-8") == "foo\nbar\n"


def test_add_local_exclude_appends_newline_when_missing(tmp_path):
    client = GitClient()
    repo = tmp_path / "repo"
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True)
    exclude.write_text("existing", encoding="utf-8")  # no trailing newline
    client.add_local_exclude(repo, "new")
    assert exclude.read_text(encoding="utf-8") == "existing\nnew\n"


def test_add_local_exclude_existing_with_newline(tmp_path):
    client = GitClient()
    repo = tmp_path / "repo"
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True)
    exclude.write_text("existing\n", encoding="utf-8")
    client.add_local_exclude(repo, "new")
    assert exclude.read_text(encoding="utf-8") == "existing\nnew\n"


# ---------------------------------------------------------------------------
# verify_remote_branch_exists
# ---------------------------------------------------------------------------


def test_verify_remote_branch_exists_ok(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", return_value="abc123\trefs/heads/main") as run:
        result = client.verify_remote_branch_exists(tmp_path, "main")
    assert result is None
    run.assert_called_once()


def test_verify_remote_branch_exists_missing(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", return_value=""):
        with pytest.raises(ValueError, match="Base branch does not exist"):
            client.verify_remote_branch_exists(tmp_path, "main")


# ---------------------------------------------------------------------------
# remote_default_branch
# ---------------------------------------------------------------------------


def test_remote_default_branch(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", return_value="refs/remotes/origin/main"):
        assert client.remote_default_branch(tmp_path) == "main"


# ---------------------------------------------------------------------------
# create_remote_branch_from
# ---------------------------------------------------------------------------


def test_create_remote_branch_from(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", return_value="") as run:
        client.create_remote_branch_from(tmp_path, "feat", "origin/main")
    assert run.call_args_list == [
        call(["git", "push", "origin", "origin/main:refs/heads/feat"], cwd=tmp_path),
        call(
            ["git", "fetch", "origin", "feat:refs/remotes/origin/feat"],
            cwd=tmp_path,
        ),
    ]


# ---------------------------------------------------------------------------
# checkout_base
# ---------------------------------------------------------------------------


def test_checkout_base_pull_succeeds(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", return_value="") as run:
        client.checkout_base(tmp_path, "main")
    assert run.call_args_list == [
        call(["git", "checkout", "main"], cwd=tmp_path),
        call(["git", "pull", "--ff-only"], cwd=tmp_path),
    ]


def test_checkout_base_pull_failure_swallowed(tmp_path):
    client = GitClient()

    def side(args, cwd=None):
        if args[:2] == ["git", "pull"]:
            raise RuntimeError("no upstream")
        return ""

    with patch.object(client, "_run", side_effect=side) as run:
        result = client.checkout_base(tmp_path, "main")
    assert result is None
    run.assert_any_call(["git", "checkout", "main"], cwd=tmp_path)
    run.assert_any_call(["git", "pull", "--ff-only"], cwd=tmp_path)


# ---------------------------------------------------------------------------
# restore_to_head
# ---------------------------------------------------------------------------


def test_restore_to_head_ok(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", return_value="") as run:
        client.restore_to_head(tmp_path, "file.json")
    run.assert_called_once_with(["git", "checkout", "HEAD", "--", "file.json"], cwd=tmp_path)


def test_restore_to_head_error_swallowed(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", side_effect=RuntimeError("absent")) as run:
        result = client.restore_to_head(tmp_path, "file.json")
    assert result is None
    run.assert_called_once_with(["git", "checkout", "HEAD", "--", "file.json"], cwd=tmp_path)


# ---------------------------------------------------------------------------
# create_task_branch
# ---------------------------------------------------------------------------


def test_create_task_branch_existing_on_remote(tmp_path):
    client = GitClient()
    calls = []

    def side(args, cwd=None):
        calls.append(args)
        if args[:3] == ["git", "ls-remote", "--heads"]:
            return "sha\trefs/heads/task"
        return ""

    with patch.object(client, "_run", side_effect=side):
        existed = client.create_task_branch(tmp_path, "task")
    assert existed is True
    assert ["git", "fetch", "origin", "task:refs/remotes/origin/task"] in calls
    assert ["git", "checkout", "-b", "task", "origin/task"] in calls


def test_create_task_branch_new(tmp_path):
    client = GitClient()

    def side(args, cwd=None):
        if args[:3] == ["git", "ls-remote", "--heads"]:
            return ""
        return ""

    with patch.object(client, "_run", side_effect=side) as run:
        existed = client.create_task_branch(tmp_path, "task")
    assert existed is False
    run.assert_any_call(["git", "checkout", "-b", "task"], cwd=tmp_path)


# ---------------------------------------------------------------------------
# try_merge_base
# ---------------------------------------------------------------------------


def test_try_merge_base_success(tmp_path):
    client = GitClient()
    with patch("subprocess.run", return_value=_proc(returncode=0)) as sr:
        ok, conflicts = client.try_merge_base(tmp_path, "main")
    assert ok is True
    assert conflicts == []
    sr.assert_called_once()


def test_try_merge_base_conflict(tmp_path):
    client = GitClient()
    results = [
        _proc(returncode=1),  # merge fails
        _proc(returncode=0, stdout="a.py\n b.py \n\n"),  # diff
    ]
    with patch("subprocess.run", side_effect=results):
        ok, conflicts = client.try_merge_base(tmp_path, "main")
    assert ok is False
    assert conflicts == ["a.py", "b.py"]


def test_try_merge_base_conflict_diff_command_fails(tmp_path, caplog):
    client = GitClient()
    results = [
        _proc(returncode=1),  # merge fails
        _proc(returncode=128, stdout="", stderr="fatal: bad"),  # diff fails
    ]
    with patch("subprocess.run", side_effect=results):
        ok, conflicts = client.try_merge_base(tmp_path, "main")
    assert ok is False
    assert conflicts == []
    assert any("diff --diff-filter=U failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# recent_commits / recent_changed_files
# ---------------------------------------------------------------------------


def test_recent_commits(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", return_value="aaa one\n  bbb two  \n\n"):
        out = client.recent_commits(tmp_path, max_count=2)
    assert out == ["aaa one", "bbb two"]


def test_recent_changed_files_dedups_and_normalizes(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", return_value="./a/b.py\na/b.py\nc.py\n"):
        out = client.recent_changed_files(tmp_path)
    assert out == ["a/b.py", "c.py"]


# ---------------------------------------------------------------------------
# set_identity
# ---------------------------------------------------------------------------


def test_set_identity(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", return_value="") as run:
        client.set_identity(tmp_path, "Bot", "bot@example.com")
    assert run.call_args_list == [
        call(["git", "config", "user.name", "Bot"], cwd=tmp_path),
        call(["git", "config", "user.email", "bot@example.com"], cwd=tmp_path),
    ]


# ---------------------------------------------------------------------------
# changed_files
# ---------------------------------------------------------------------------


def test_changed_files(tmp_path):
    client = GitClient()
    diff = b"M\x00a.py\x00R100\x00old.py\x00new.py\x00"
    untracked = b"./c.py\x00d.py\x00"
    with patch.object(client, "_run_bytes", side_effect=[diff, untracked]):
        out = client.changed_files(tmp_path)
    assert out == sorted({"a.py", "new.py", "c.py", "d.py"})


# ---------------------------------------------------------------------------
# diff_stat
# ---------------------------------------------------------------------------


def test_diff_stat(tmp_path):
    client = GitClient()
    with (
        patch.object(client, "_run", return_value=" a.py | 2 ++\n\n  \n 1 file"),
        patch.object(client, "_run_bytes", return_value=b"./new.py\x00"),
    ):
        out = client.diff_stat(tmp_path)
    assert "a.py | 2 ++" in out
    assert " untracked | new.py" in out
    assert " 1 file" in out


# ---------------------------------------------------------------------------
# diff_patch
# ---------------------------------------------------------------------------


def test_diff_patch(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", return_value="diff --git") as run:
        assert client.diff_patch(tmp_path) == "diff --git"
    run.assert_called_once_with(["git", "diff", "--binary", "HEAD"], cwd=tmp_path)


# ---------------------------------------------------------------------------
# commit_all
# ---------------------------------------------------------------------------


def test_commit_all_with_changes(tmp_path):
    client = GitClient()

    def side(args, cwd=None):
        if args[:2] == ["git", "status"]:
            return " M a.py"
        return ""

    with patch.object(client, "_run", side_effect=side) as run:
        committed = client.commit_all(tmp_path, "msg")
    assert committed is True
    run.assert_any_call(["git", "commit", "-m", "msg"], cwd=tmp_path)


def test_commit_all_no_changes(tmp_path):
    client = GitClient()

    def side(args, cwd=None):
        if args[:2] == ["git", "status"]:
            return ""
        return ""

    with patch.object(client, "_run", side_effect=side) as run:
        committed = client.commit_all(tmp_path, "msg")
    assert committed is False
    for c in run.call_args_list:
        assert c.args[0][:2] != ["git", "commit"]


# ---------------------------------------------------------------------------
# push_branch / push_branch_force
# ---------------------------------------------------------------------------


def test_push_branch(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", return_value="") as run:
        client.push_branch(tmp_path, "feat")
    run.assert_called_once_with(["git", "push", "-u", "origin", "feat"], cwd=tmp_path)


def test_push_branch_force(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", return_value="") as run:
        client.push_branch_force(tmp_path, "feat")
    run.assert_called_once_with(
        ["git", "push", "--force-with-lease", "origin", "feat"], cwd=tmp_path
    )


# ---------------------------------------------------------------------------
# squash_commits
# ---------------------------------------------------------------------------


def test_squash_commits_performed(tmp_path):
    client = GitClient()

    def side(args, cwd=None):
        if args[:3] == ["git", "rev-list", "--count"]:
            return "3"
        if args[:2] == ["git", "merge-base"]:
            return "basesha\n"
        return ""

    with patch.object(client, "_run", side_effect=side) as run:
        result = client.squash_commits(tmp_path, "main", "squashed")
    assert result is True
    run.assert_any_call(["git", "reset", "--soft", "basesha"], cwd=tmp_path)
    run.assert_any_call(["git", "commit", "-m", "squashed"], cwd=tmp_path)


def test_squash_commits_single_commit_noop(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", return_value="1"):
        assert client.squash_commits(tmp_path, "main", "x") is False


def test_squash_commits_count_error(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", side_effect=RuntimeError("no ref")):
        assert client.squash_commits(tmp_path, "main", "x") is False


def test_squash_commits_count_not_int(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", return_value="notanumber"):
        assert client.squash_commits(tmp_path, "main", "x") is False


# ---------------------------------------------------------------------------
# checkout_branch
# ---------------------------------------------------------------------------


def test_checkout_branch(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", return_value="") as run:
        client.checkout_branch(tmp_path, "feat")
    run.assert_called_once_with(["git", "checkout", "feat"], cwd=tmp_path)


# ---------------------------------------------------------------------------
# revert_commit
# ---------------------------------------------------------------------------


def test_revert_commit_success(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", return_value="") as run:
        ok = client.revert_commit(tmp_path, "deadbeef", new_branch="revert-x")
    assert ok is True
    run.assert_any_call(["git", "checkout", "-b", "revert-x"], cwd=tmp_path)
    run.assert_any_call(["git", "revert", "--no-edit", "deadbeef"], cwd=tmp_path)


def test_revert_commit_conflict_aborts(tmp_path):
    client = GitClient()
    calls = []

    def side(args, cwd=None):
        calls.append(args)
        if args[:2] == ["git", "revert"] and "--abort" not in args:
            raise RuntimeError("conflict")
        return ""

    with patch.object(client, "_run", side_effect=side):
        ok = client.revert_commit(tmp_path, "deadbeef", new_branch="revert-x")
    assert ok is False
    assert ["git", "revert", "--abort"] in calls


def test_revert_commit_conflict_abort_also_fails(tmp_path):
    client = GitClient()

    def side(args, cwd=None):
        if args[:2] == ["git", "revert"]:
            raise RuntimeError("conflict")
        return ""

    with patch.object(client, "_run", side_effect=side):
        ok = client.revert_commit(tmp_path, "deadbeef", new_branch="revert-x")
    assert ok is False


# ---------------------------------------------------------------------------
# rebase_onto_origin
# ---------------------------------------------------------------------------


def test_rebase_onto_origin_success(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", return_value="") as run:
        assert client.rebase_onto_origin(tmp_path, "main") is True
    run.assert_any_call(["git", "fetch", "origin", "main"], cwd=tmp_path)
    run.assert_any_call(["git", "rebase", "origin/main"], cwd=tmp_path)


def test_rebase_onto_origin_fetch_fails(tmp_path):
    client = GitClient()
    with patch.object(client, "_run", side_effect=RuntimeError("no network")):
        assert client.rebase_onto_origin(tmp_path, "main") is False


def test_rebase_onto_origin_conflict_aborts(tmp_path):
    client = GitClient()
    calls = []

    def side(args, cwd=None):
        calls.append(args)
        if args[:2] == ["git", "rebase"] and "--abort" not in args:
            raise RuntimeError("conflict")
        return ""

    with patch.object(client, "_run", side_effect=side):
        assert client.rebase_onto_origin(tmp_path, "main") is False
    assert ["git", "rebase", "--abort"] in calls


def test_rebase_onto_origin_conflict_abort_also_fails(tmp_path):
    client = GitClient()

    def side(args, cwd=None):
        if args[:2] == ["git", "rebase"]:
            raise RuntimeError("conflict")
        return ""

    with patch.object(client, "_run", side_effect=side):
        assert client.rebase_onto_origin(tmp_path, "main") is False


# ---------------------------------------------------------------------------
# _parse_name_status_output
# ---------------------------------------------------------------------------


def test_parse_name_status_empty():
    client = GitClient()
    assert client._parse_name_status_output(b"") == []


def test_parse_name_status_modified_and_rename():
    client = GitClient()
    out = b"M\x00a.py\x00R100\x00old.py\x00new.py\x00A\x00c.py\x00"
    assert client._parse_name_status_output(out) == ["a.py", "new.py", "c.py"]


def test_parse_name_status_rename_truncated():
    client = GitClient()
    # rename status with missing new path -> break
    out = b"R100\x00old.py\x00"
    assert client._parse_name_status_output(out) == []


def test_parse_name_status_simple_truncated():
    client = GitClient()
    # status code but no following path -> break
    out = b"M\x00"
    assert client._parse_name_status_output(out) == []


# ---------------------------------------------------------------------------
# _parse_null_delimited_paths
# ---------------------------------------------------------------------------


def test_parse_null_delimited_empty():
    client = GitClient()
    assert client._parse_null_delimited_paths(b"") == []


def test_parse_null_delimited():
    client = GitClient()
    assert client._parse_null_delimited_paths(b"a\x00b\x00") == ["a", "b"]


# ---------------------------------------------------------------------------
# _normalize_repo_relative_path
# ---------------------------------------------------------------------------


def test_normalize_repo_relative_path():
    client = GitClient()
    assert client._normalize_repo_relative_path("./a/b.py") == "a/b.py"
    assert client._normalize_repo_relative_path("a\\b.py") == "a/b.py"


# ---------------------------------------------------------------------------
# branch_allowed
# ---------------------------------------------------------------------------


def test_branch_allowed_no_patterns():
    assert branch_allowed("main", []) is True


def test_branch_allowed_match():
    assert branch_allowed("release/1.0", ["release/*"]) is True


def test_branch_allowed_no_match():
    assert branch_allowed("feature/x", ["release/*", "main"]) is False
