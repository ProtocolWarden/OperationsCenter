# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from operations_center.backends.openclaw import normalize as norm
from operations_center.backends.openclaw.models import (
    OpenClawArtifactCapture,
    OpenClawRunCapture,
)
from operations_center.contracts.common import ChangedFileRef
from operations_center.contracts.execution import RuntimeInvocationRef
from operations_center.contracts.enums import (
    ArtifactType,
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _capture(**kwargs) -> OpenClawRunCapture:
    base = dict(
        run_id="run-1",
        outcome="success",
        exit_code=0,
        output_text="ok",
        error_text="",
    )
    base.update(kwargs)
    return OpenClawRunCapture(**base)


class _FakeProc:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


# ---------------------------------------------------------------------------
# normalize — happy path (success)
# ---------------------------------------------------------------------------


def test_normalize_success_basic_fields() -> None:
    cap = _capture()
    result = norm.normalize(cap, proposal_id="p1", decision_id="d1")
    assert result.run_id == "run-1"
    assert result.proposal_id == "p1"
    assert result.decision_id == "d1"
    assert result.success is True
    assert result.status == ExecutionStatus.SUCCEEDED
    # No failure info on success.
    assert result.failure_category is None
    assert result.failure_reason is None
    assert result.branch_pushed is False
    assert result.pull_request_url is None


def test_normalize_success_no_workspace_no_reported_is_unknown() -> None:
    cap = _capture()
    result = norm.normalize(cap, proposal_id="p1", decision_id="d1")
    assert result.changed_files == []
    assert result.changed_files_source == "unknown"
    assert result.changed_files_confidence == 0.0
    assert result.diff_stat_excerpt is None
    # The capture's source is mutated to the resolved value.
    assert cap.changed_files_source == "unknown"


def test_normalize_passes_branch_and_invocation_ref() -> None:
    ref = RuntimeInvocationRef(
        invocation_id="inv-ref-123",
        runtime_name="direct_local",
        runtime_kind="subprocess",
    )
    cap = _capture(invocation_ref=ref)
    result = norm.normalize(
        cap, proposal_id="p", decision_id="d", branch_name="feat/x"
    )
    assert result.branch_name == "feat/x"
    assert result.runtime_invocation_ref.invocation_id == "inv-ref-123"


def test_normalize_missing_invocation_ref_attr_is_none() -> None:
    cap = _capture()
    # Simulate an object lacking the attribute entirely.
    object.__setattr__  # noqa: B018  (just touch to keep intent clear)
    delattr(cap, "invocation_ref")
    result = norm.normalize(cap, proposal_id="p", decision_id="d")
    assert result.runtime_invocation_ref is None


# ---------------------------------------------------------------------------
# normalize — failure mapping
# ---------------------------------------------------------------------------


def test_normalize_failure_sets_failed_status_and_failure_info() -> None:
    cap = _capture(outcome="failure", output_text="boom", error_text="bad thing")
    result = norm.normalize(cap, proposal_id="p", decision_id="d")
    assert result.success is False
    assert result.status == ExecutionStatus.FAILED
    assert result.failure_category == FailureReasonCategory.BACKEND_ERROR
    assert "openclaw failed" in result.failure_reason


def test_normalize_timeout_outcome_maps_timed_out() -> None:
    cap = _capture(outcome="timeout")
    result = norm.normalize(cap, proposal_id="p", decision_id="d")
    assert result.status == ExecutionStatus.TIMED_OUT
    assert result.failure_category == FailureReasonCategory.TIMEOUT
    assert result.failure_reason == "openclaw execution timed out"


def test_normalize_timeout_hit_flag_maps_timed_out() -> None:
    cap = _capture(outcome="failure", timeout_hit=True)
    result = norm.normalize(cap, proposal_id="p", decision_id="d")
    assert result.status == ExecutionStatus.TIMED_OUT


def test_normalize_partial_outcome_failure_reason() -> None:
    cap = _capture(outcome="partial", output_text="halfway", error_text="")
    result = norm.normalize(cap, proposal_id="p", decision_id="d")
    assert result.success is False
    assert result.status == ExecutionStatus.FAILED
    assert result.failure_reason.startswith("openclaw completed partially")
    assert "halfway" in result.failure_reason


# ---------------------------------------------------------------------------
# _resolve_changed_files / git diff path
# ---------------------------------------------------------------------------


def test_resolve_uses_git_diff_when_workspace_and_git_succeeds() -> None:
    cap = _capture(reported_changed_files=[{"path": "ignored.py"}])
    proc = _FakeProc(0, "A\tnew.py\nM\tmod.py\n")
    with mock.patch("subprocess.run", return_value=proc) as run:
        files, source = norm._resolve_changed_files(cap, Path("/ws"))
    assert source == "git_diff"
    assert [f.path for f in files] == ["new.py", "mod.py"]
    assert [f.change_type for f in files] == ["added", "modified"]
    run.assert_called_once()


def test_resolve_empty_workspace_string_skips_git() -> None:
    cap = _capture(reported_changed_files=[{"path": "a.py"}])
    # workspace_path stringifies to "." → treated as no workspace.
    files, source = norm._resolve_changed_files(cap, Path("."))
    assert source == "event_stream"
    assert files[0].path == "a.py"


def test_resolve_falls_back_to_event_stream_when_git_returns_none() -> None:
    cap = _capture(reported_changed_files=[{"path": "b.py", "change_type": "deleted"}])
    with mock.patch.object(
        norm, "_discover_changed_files_via_git", return_value=None
    ):
        files, source = norm._resolve_changed_files(cap, Path("/ws"))
    assert source == "event_stream"
    assert files[0].change_type == "deleted"


def test_resolve_reported_present_but_all_empty_yields_unknown() -> None:
    # Entries with no usable path produce empty inferred list → unknown.
    cap = _capture(reported_changed_files=[{"path": ""}, {"foo": "bar"}])
    files, source = norm._resolve_changed_files(cap, None)
    assert files == []
    assert source == "unknown"


def test_resolve_no_workspace_no_reported_unknown() -> None:
    cap = _capture()
    files, source = norm._resolve_changed_files(cap, None)
    assert files == []
    assert source == "unknown"


def test_resolve_git_empty_diff_returns_known_empty() -> None:
    # git succeeds but reports nothing changed → authoritative empty list.
    cap = _capture(reported_changed_files=[{"path": "x.py"}])
    proc = _FakeProc(0, "")
    with mock.patch("subprocess.run", return_value=proc):
        files, source = norm._resolve_changed_files(cap, Path("/ws"))
    assert files == []
    assert source == "git_diff"


# ---------------------------------------------------------------------------
# _discover_changed_files_via_git
# ---------------------------------------------------------------------------


def test_discover_git_nonzero_returncode_returns_none() -> None:
    proc = _FakeProc(128, "")
    with mock.patch("subprocess.run", return_value=proc):
        assert norm._discover_changed_files_via_git(Path("/ws")) is None


def test_discover_git_skips_malformed_lines() -> None:
    proc = _FakeProc(0, "A\tgood.py\nmalformed-line-no-tab\nR\trenamed.py\n")
    with mock.patch("subprocess.run", return_value=proc):
        refs = norm._discover_changed_files_via_git(Path("/ws"))
    assert [r.path for r in refs] == ["good.py", "renamed.py"]
    assert [r.change_type for r in refs] == ["added", "renamed"]


def test_discover_git_strips_path_whitespace() -> None:
    proc = _FakeProc(0, "M\t  spaced.py  ")
    with mock.patch("subprocess.run", return_value=proc):
        refs = norm._discover_changed_files_via_git(Path("/ws"))
    assert refs[0].path == "spaced.py"


def test_discover_git_exception_returns_none() -> None:
    with mock.patch("subprocess.run", side_effect=OSError("git missing")):
        assert norm._discover_changed_files_via_git(Path("/ws")) is None


# ---------------------------------------------------------------------------
# _parse_reported_changed_files
# ---------------------------------------------------------------------------


def test_parse_reported_defaults_change_type_modified() -> None:
    refs = norm._parse_reported_changed_files([{"path": "a.py"}])
    assert refs[0].path == "a.py"
    assert refs[0].change_type == "modified"


def test_parse_reported_skips_missing_path() -> None:
    refs = norm._parse_reported_changed_files(
        [{"path": ""}, {}, {"path": "kept.py", "change_type": "added"}]
    )
    assert len(refs) == 1
    assert refs[0].path == "kept.py"
    assert refs[0].change_type == "added"


# ---------------------------------------------------------------------------
# _git_status_to_change_type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        ("A", "added"),
        ("M", "modified"),
        ("D", "deleted"),
        ("R", "renamed"),
        ("a", "added"),
        ("R100", "renamed"),
        ("X", "modified"),
        ("?", "modified"),
    ],
)
def test_git_status_to_change_type(status: str, expected: str) -> None:
    assert norm._git_status_to_change_type(status) == expected


# ---------------------------------------------------------------------------
# _build_diff_stat
# ---------------------------------------------------------------------------


def test_build_diff_stat_empty_is_none() -> None:
    assert norm._build_diff_stat([]) is None


def test_build_diff_stat_singular() -> None:
    refs = [ChangedFileRef(path="a.py")]
    assert norm._build_diff_stat(refs) == "1 file changed"


def test_build_diff_stat_plural() -> None:
    refs = [ChangedFileRef(path="a.py"), ChangedFileRef(path="b.py")]
    assert norm._build_diff_stat(refs) == "2 files changed"


# ---------------------------------------------------------------------------
# _changed_files_confidence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source,expected",
    [
        ("git_diff", 1.0),
        ("event_stream", 0.5),
        ("unknown", 0.0),
        ("anything_else", 0.0),
    ],
)
def test_changed_files_confidence(source: str, expected: float) -> None:
    assert norm._changed_files_confidence(source) == expected


# ---------------------------------------------------------------------------
# _build_validation_summary
# ---------------------------------------------------------------------------


def test_validation_not_ran_skipped() -> None:
    vs = norm._build_validation_summary(
        ran=False, passed=None, excerpt=None, duration_ms=None
    )
    assert vs.status == ValidationStatus.SKIPPED
    assert vs.commands_run == 0


def test_validation_passed() -> None:
    vs = norm._build_validation_summary(
        ran=True, passed=True, excerpt=None, duration_ms=1200
    )
    assert vs.status == ValidationStatus.PASSED
    assert vs.commands_run == 1
    assert vs.commands_passed == 1
    assert vs.commands_failed == 0
    assert vs.duration_ms == 1200


def test_validation_failed_with_excerpt() -> None:
    vs = norm._build_validation_summary(
        ran=True, passed=False, excerpt="assert error", duration_ms=50
    )
    assert vs.status == ValidationStatus.FAILED
    assert vs.commands_failed == 1
    assert vs.commands_passed == 0
    assert vs.failure_excerpt == "assert error"
    assert vs.duration_ms == 50


def test_validation_ran_but_passed_none_skipped() -> None:
    vs = norm._build_validation_summary(
        ran=True, passed=None, excerpt="ignored", duration_ms=10
    )
    assert vs.status == ValidationStatus.SKIPPED


def test_normalize_threads_validation_through() -> None:
    cap = _capture()
    result = norm.normalize(
        cap,
        proposal_id="p",
        decision_id="d",
        validation_ran=True,
        validation_passed=False,
        validation_excerpt="ouch",
        validation_duration_ms=7,
    )
    assert result.validation.status == ValidationStatus.FAILED
    assert result.validation.failure_excerpt == "ouch"
    assert result.validation.duration_ms == 7


# ---------------------------------------------------------------------------
# _map_artifacts
# ---------------------------------------------------------------------------


def test_map_artifacts_known_type() -> None:
    cap = _capture(
        artifacts=[
            OpenClawArtifactCapture(
                label="diff", content="patch text", artifact_type="diff"
            )
        ]
    )
    arts = norm._map_artifacts(cap)
    assert len(arts) == 1
    assert arts[0].artifact_type == ArtifactType.DIFF
    assert arts[0].label == "diff"
    assert arts[0].content == "patch text"


def test_map_artifacts_unknown_type_falls_back_to_log_excerpt() -> None:
    cap = _capture(
        artifacts=[
            OpenClawArtifactCapture(
                label="weird", content="data", artifact_type="not_a_real_type"
            )
        ]
    )
    arts = norm._map_artifacts(cap)
    assert arts[0].artifact_type == ArtifactType.LOG_EXCERPT


def test_map_artifacts_empty_content_becomes_none() -> None:
    cap = _capture(
        artifacts=[
            OpenClawArtifactCapture(label="empty", content="", artifact_type="diff")
        ]
    )
    arts = norm._map_artifacts(cap)
    assert arts[0].content is None


def test_map_artifacts_empty_list() -> None:
    cap = _capture()
    assert norm._map_artifacts(cap) == []


# ---------------------------------------------------------------------------
# _extract_failure_info
# ---------------------------------------------------------------------------


def test_extract_failure_info_success_returns_none() -> None:
    cap = _capture(outcome="success")
    assert norm._extract_failure_info(cap) is None


def test_extract_failure_info_timeout_flags() -> None:
    cap = _capture(outcome="timeout")
    info = norm._extract_failure_info(cap)
    assert info is not None
    assert info.is_timeout is True
    assert info.is_partial is False
    assert info.failure_category_value == FailureReasonCategory.TIMEOUT.value


def test_extract_failure_info_partial_flags() -> None:
    cap = _capture(outcome="partial")
    info = norm._extract_failure_info(cap)
    assert info.is_partial is True
    assert info.is_timeout is False


def test_extract_failure_info_timeout_hit_without_timeout_outcome() -> None:
    cap = _capture(outcome="failure", timeout_hit=True)
    info = norm._extract_failure_info(cap)
    assert info.is_timeout is True


# ---------------------------------------------------------------------------
# _map_failure_status
# ---------------------------------------------------------------------------


def test_map_failure_status_plain_failure() -> None:
    cap = _capture(outcome="failure")
    assert norm._map_failure_status(cap) == ExecutionStatus.FAILED


def test_map_failure_status_timeout_outcome() -> None:
    cap = _capture(outcome="timeout")
    assert norm._map_failure_status(cap) == ExecutionStatus.TIMED_OUT


# ---------------------------------------------------------------------------
# integration: full normalize with git diff + artifacts + failure category
# ---------------------------------------------------------------------------


def test_normalize_full_failure_with_git_diff_and_validation_signals() -> None:
    cap = _capture(
        outcome="failure",
        output_text="tests failed in suite",
        error_text="",
        artifacts=[
            OpenClawArtifactCapture(
                label="log", content="trace", artifact_type="log_excerpt"
            )
        ],
    )
    proc = _FakeProc(0, "M\tsrc/a.py\nA\tsrc/b.py\n")
    with mock.patch("subprocess.run", return_value=proc):
        result = norm.normalize(
            cap,
            proposal_id="p",
            decision_id="d",
            branch_name="auto/fix",
            workspace_path=Path("/workspace"),
            validation_ran=True,
            validation_passed=False,
            validation_excerpt="2 failed",
        )
    assert result.status == ExecutionStatus.FAILED
    assert result.changed_files_source == "git_diff"
    assert result.changed_files_confidence == 1.0
    assert result.diff_stat_excerpt == "2 files changed"
    # "tests failed" signal categorized as validation failure.
    assert result.failure_category == FailureReasonCategory.VALIDATION_FAILED
    assert len(result.artifacts) == 1
    assert cap.changed_files_source == "git_diff"
