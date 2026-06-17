# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from operations_center.post_merge_regression import (
    RegressionSignal,
    _extract_evidence_file_tokens,
    create_revert_branch,
    detect_post_merge_regressions,
)


def _now_iso(delta_hours: float = 0.0) -> str:
    return (datetime.now(UTC) + timedelta(hours=delta_hours)).isoformat()


# --------------------------------------------------------------------------- #
# detect_post_merge_regressions
# --------------------------------------------------------------------------- #


def test_detect_no_head_sha_returns_empty():
    gh = MagicMock()
    gh.get_branch_head.return_value = None
    out = detect_post_merge_regressions(gh, "o", "r")
    assert out == []
    gh.get_failed_checks.assert_not_called()


def test_detect_failed_checks_fetch_raises_returns_empty():
    gh = MagicMock()
    gh.get_branch_head.return_value = "abc123"
    gh.get_failed_checks.side_effect = RuntimeError("boom")
    out = detect_post_merge_regressions(gh, "o", "r")
    assert out == []


def test_detect_base_green_returns_empty():
    gh = MagicMock()
    gh.get_branch_head.return_value = "abc123"
    gh.get_failed_checks.return_value = []
    out = detect_post_merge_regressions(gh, "o", "r")
    assert out == []


def test_detect_no_list_merged_attribute_attributes_head():
    # spec= without list_recently_merged_prs -> getattr returns None
    gh = MagicMock(spec=["get_branch_head", "get_failed_checks"])
    gh.get_branch_head.return_value = "headsha"
    gh.get_failed_checks.return_value = ["build", "lint"]
    out = detect_post_merge_regressions(gh, "o", "r", base_branch="develop")
    assert len(out) == 1
    sig = out[0]
    assert sig.pr_number is None
    assert sig.merge_commit_sha == "headsha"
    assert sig.head_sha == "headsha"
    assert sig.failed_checks == ("build", "lint")
    assert sig.base_branch == "develop"
    assert sig.merged_at  # isoformat string present


def test_detect_list_merged_not_callable_attributes_head():
    gh = MagicMock(spec=["get_branch_head", "get_failed_checks"])
    gh.get_branch_head.return_value = "headsha"
    gh.get_failed_checks.return_value = ["build"]
    # Add a non-callable attribute named list_recently_merged_prs
    gh.list_recently_merged_prs = "not-callable"
    out = detect_post_merge_regressions(gh, "o", "r")
    assert len(out) == 1
    assert out[0].pr_number is None


def test_detect_list_merged_raises_yields_no_signals():
    gh = MagicMock()
    gh.get_branch_head.return_value = "headsha"
    gh.get_failed_checks.return_value = ["build"]
    gh.list_recently_merged_prs.side_effect = RuntimeError("api down")
    out = detect_post_merge_regressions(gh, "o", "r")
    assert out == []


def test_detect_pr_within_lookback_attributed():
    gh = MagicMock()
    gh.get_branch_head.return_value = "headsha"
    gh.get_failed_checks.return_value = ["build"]
    gh.list_recently_merged_prs.return_value = [
        {
            "number": 42,
            "merged_at": _now_iso(-1),
            "merge_commit_sha": "mergesha42",
        }
    ]
    out = detect_post_merge_regressions(gh, "o", "r", lookback_hours=24)
    assert len(out) == 1
    sig = out[0]
    assert sig.pr_number == 42
    assert sig.merge_commit_sha == "mergesha42"
    assert sig.head_sha == "headsha"
    assert sig.failed_checks == ("build",)


def test_detect_pr_older_than_cutoff_skipped():
    gh = MagicMock()
    gh.get_branch_head.return_value = "headsha"
    gh.get_failed_checks.return_value = ["build"]
    gh.list_recently_merged_prs.return_value = [
        {"number": 1, "merged_at": _now_iso(-100), "merge_commit_sha": "old"}
    ]
    out = detect_post_merge_regressions(gh, "o", "r", lookback_hours=24)
    assert out == []


def test_detect_pr_unparseable_date_skipped():
    gh = MagicMock()
    gh.get_branch_head.return_value = "headsha"
    gh.get_failed_checks.return_value = ["build"]
    gh.list_recently_merged_prs.return_value = [
        {"number": 1, "merged_at": "not-a-date", "merge_commit_sha": "x"},
    ]
    out = detect_post_merge_regressions(gh, "o", "r")
    assert out == []


def test_detect_pr_none_date_skipped():
    gh = MagicMock()
    gh.get_branch_head.return_value = "headsha"
    gh.get_failed_checks.return_value = ["build"]
    gh.list_recently_merged_prs.return_value = [
        {"number": 1, "merged_at": None, "updated_at": None, "merge_commit_sha": "x"},
    ]
    out = detect_post_merge_regressions(gh, "o", "r")
    assert out == []


def test_detect_uses_updated_at_fallback():
    gh = MagicMock()
    gh.get_branch_head.return_value = "headsha"
    gh.get_failed_checks.return_value = ["build"]
    gh.list_recently_merged_prs.return_value = [
        {"number": 7, "updated_at": _now_iso(-1), "merge_commit_sha": "s7"},
    ]
    out = detect_post_merge_regressions(gh, "o", "r")
    assert len(out) == 1
    assert out[0].pr_number == 7


def test_detect_z_suffix_date_and_missing_fields():
    gh = MagicMock()
    gh.get_branch_head.return_value = "headsha"
    gh.get_failed_checks.return_value = ["build"]
    # Zulu suffix, missing number -> pr_number None, missing merge_commit_sha -> ""
    raw = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    gh.list_recently_merged_prs.return_value = [{"merged_at": raw}]
    out = detect_post_merge_regressions(gh, "o", "r")
    assert len(out) == 1
    sig = out[0]
    assert sig.pr_number is None  # int(0) or None -> None
    assert sig.merge_commit_sha == ""


def test_detect_number_zero_becomes_none():
    gh = MagicMock()
    gh.get_branch_head.return_value = "headsha"
    gh.get_failed_checks.return_value = ["build"]
    gh.list_recently_merged_prs.return_value = [
        {"number": 0, "merged_at": _now_iso(-1), "merge_commit_sha": "s"},
    ]
    out = detect_post_merge_regressions(gh, "o", "r")
    assert out[0].pr_number is None


def test_detect_ignored_checks_passed_through():
    gh = MagicMock()
    gh.get_branch_head.return_value = "headsha"
    gh.get_failed_checks.return_value = ["build"]
    gh.list_recently_merged_prs.return_value = []
    detect_post_merge_regressions(gh, "o", "r", ignored_checks=("flaky",))
    _, kwargs = gh.get_failed_checks.call_args
    assert kwargs["ignored_checks"] == ["flaky"]


# --------------------------------------------------------------------------- #
# create_revert_branch
# --------------------------------------------------------------------------- #


def test_create_revert_branch_success(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return MagicMock(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    branch = create_revert_branch(tmp_path, commit_sha="abcdef1234567890")
    assert branch == "revert/abcdef12"
    assert len(calls) == 3
    assert calls[0][:2] == ["git", "fetch"]
    assert calls[2][:2] == ["git", "revert"]


def test_create_revert_branch_custom_name(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: MagicMock(returncode=0))
    branch = create_revert_branch(
        tmp_path, commit_sha="deadbeef", base_branch="develop", branch_name="my-revert"
    )
    assert branch == "my-revert"


def test_create_revert_branch_empty_sha_uses_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: MagicMock(returncode=0))
    branch = create_revert_branch(tmp_path, commit_sha="")
    assert branch == "revert/unknown"


def test_create_revert_branch_called_process_error_with_stderr(tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr=b"conflict happened")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert create_revert_branch(tmp_path, commit_sha="abc12345") is None


def test_create_revert_branch_called_process_error_no_stderr(tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr=None)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert create_revert_branch(tmp_path, commit_sha="abc12345") is None


def test_create_revert_branch_timeout(tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 60)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert create_revert_branch(tmp_path, commit_sha="abc12345") is None


# --------------------------------------------------------------------------- #
# _extract_evidence_file_tokens
# --------------------------------------------------------------------------- #


def test_extract_tokens_empty():
    assert _extract_evidence_file_tokens("") == ()


def test_extract_tokens_basic():
    diff = "+++ b/src/a.py\n+++ b/src/b.py\n--- a/src/a.py\n"
    assert _extract_evidence_file_tokens(diff) == ("src/a.py", "src/b.py")


def test_extract_tokens_dedup_and_devnull():
    diff = "+++ b/src/a.py\n+++ b/src/a.py\n+++ b//dev/null\n"
    # /dev/null appears as "/dev/null" after stripping "+++ b/" -> "/dev/null"
    assert _extract_evidence_file_tokens(diff) == ("src/a.py",)


def test_extract_tokens_blank_path_skipped():
    diff = "+++ b/\n+++ b/real.py\n"
    assert _extract_evidence_file_tokens(diff) == ("real.py",)


def test_extract_tokens_respects_max_files():
    lines = "\n".join(f"+++ b/f{i}.py" for i in range(20))
    out = _extract_evidence_file_tokens(lines, max_files=3)
    assert out == ("f0.py", "f1.py", "f2.py")


def test_extract_tokens_ignores_non_matching_lines():
    diff = "diff --git a/x b/x\nindex 123..456\n@@ -1 +1 @@\n+content\n"
    assert _extract_evidence_file_tokens(diff) == ()


# --------------------------------------------------------------------------- #
# RegressionSignal dataclass
# --------------------------------------------------------------------------- #


def test_regression_signal_frozen():
    sig = RegressionSignal(
        pr_number=1,
        merge_commit_sha="s",
        head_sha="h",
        failed_checks=("c",),
        merged_at="2026-01-01T00:00:00+00:00",
        base_branch="main",
    )
    with pytest.raises(Exception):
        sig.pr_number = 2  # type: ignore[misc]
    assert sig.failed_checks == ("c",)
