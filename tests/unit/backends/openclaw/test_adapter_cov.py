# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from operations_center.backends.openclaw import adapter as adapter_mod
from operations_center.backends.openclaw.adapter import (
    OpenClawBackendAdapter,
    _detail_dir,
    _invocation_error_result,
    _mapping_error_result,
    _unsupported_result,
)
from operations_center.backends.openclaw.models import (
    OpenClawRunCapture,
    SupportCheck,
)
from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult


def _make_request(workspace_path: str = "/tmp/ws", **overrides) -> ExecutionRequest:
    base = dict(
        proposal_id="prop-1",
        decision_id="dec-1",
        goal_text="implement the thing",
        repo_key="org/repo",
        clone_url="https://example.com/org/repo.git",
        base_branch="main",
        task_branch="task/feature-x",
        workspace_path=workspace_path,
    )
    base.update(overrides)
    return ExecutionRequest(**base)


def _make_capture(**overrides) -> OpenClawRunCapture:
    base = dict(
        run_id="run-1",
        outcome="success",
        exit_code=0,
        output_text="ok",
        error_text="",
    )
    base.update(overrides)
    return OpenClawRunCapture(**base)


# ---------------------------------------------------------------------------
# supports()
# ---------------------------------------------------------------------------


def test_supports_delegates_to_check_support(monkeypatch):
    sentinel = SupportCheck.yes()
    called = {}

    def _fake_check(req):
        called["req"] = req
        return sentinel

    monkeypatch.setattr(adapter_mod, "check_support", _fake_check)
    adapter = OpenClawBackendAdapter(runner=MagicMock())
    req = _make_request()

    result = adapter.supports(req)

    assert result is sentinel
    assert called["req"] is req


def test_supports_returns_no_for_invalid_request():
    adapter = OpenClawBackendAdapter(runner=MagicMock())
    req = _make_request(repo_key="")

    check = adapter.supports(req)

    assert check.supported is False
    assert "repo_key" in check.unsupported_fields


# ---------------------------------------------------------------------------
# execute_and_capture — unsupported path
# ---------------------------------------------------------------------------


def test_execute_and_capture_unsupported_returns_failed_no_capture():
    adapter = OpenClawBackendAdapter(runner=MagicMock())
    req = _make_request(goal_text="   ")  # empty after strip -> unsupported

    result, capture = adapter.execute_and_capture(req)

    assert capture is None
    assert result.status == ExecutionStatus.FAILED
    assert result.success is False
    assert result.failure_category == FailureReasonCategory.UNSUPPORTED_REQUEST
    assert "not supported" in result.failure_reason


def test_execute_returns_only_result_on_unsupported():
    adapter = OpenClawBackendAdapter(runner=MagicMock())
    req = _make_request(workspace_path=".")  # normalizes to unsupported

    result = adapter.execute(req)

    assert isinstance(result, ExecutionResult)
    assert result.failure_category == FailureReasonCategory.UNSUPPORTED_REQUEST


# ---------------------------------------------------------------------------
# execute_and_capture — mapping error path
# ---------------------------------------------------------------------------


def test_execute_and_capture_mapping_error(monkeypatch):
    monkeypatch.setattr(adapter_mod, "check_support", lambda req: SupportCheck.yes())

    def _boom(req, run_mode):
        raise RuntimeError("mapper exploded")

    monkeypatch.setattr(adapter_mod, "map_request", _boom)
    adapter = OpenClawBackendAdapter(runner=MagicMock())

    result, capture = adapter.execute_and_capture(_make_request())

    assert capture is None
    assert result.failure_category == FailureReasonCategory.BACKEND_ERROR
    assert "mapping failed" in result.failure_reason
    assert "mapper exploded" in result.failure_reason


# ---------------------------------------------------------------------------
# execute_and_capture — invocation error path
# ---------------------------------------------------------------------------


def test_execute_and_capture_invocation_error(monkeypatch):
    monkeypatch.setattr(adapter_mod, "check_support", lambda req: SupportCheck.yes())
    monkeypatch.setattr(adapter_mod, "map_request", lambda req, run_mode: MagicMock())

    adapter = OpenClawBackendAdapter(runner=MagicMock())
    adapter._invoker = MagicMock()
    adapter._invoker.invoke.side_effect = ValueError("invoke boom")

    result, capture = adapter.execute_and_capture(_make_request())

    assert capture is None
    assert result.failure_category == FailureReasonCategory.BACKEND_ERROR
    assert "invocation failed" in result.failure_reason
    assert "invoke boom" in result.failure_reason


# ---------------------------------------------------------------------------
# execute_and_capture — happy path
# ---------------------------------------------------------------------------


def test_execute_and_capture_happy_path(monkeypatch):
    monkeypatch.setattr(adapter_mod, "check_support", lambda req: SupportCheck.yes())
    prepared = MagicMock()
    monkeypatch.setattr(adapter_mod, "map_request", lambda req, run_mode: prepared)

    capture = _make_capture()
    normalized = MagicMock(spec=ExecutionResult)
    norm_calls = {}

    def _fake_normalize(*, capture, proposal_id, decision_id, branch_name, workspace_path):
        norm_calls.update(
            capture=capture,
            proposal_id=proposal_id,
            decision_id=decision_id,
            branch_name=branch_name,
            workspace_path=workspace_path,
        )
        return normalized

    monkeypatch.setattr(adapter_mod, "normalize", _fake_normalize)

    adapter = OpenClawBackendAdapter(runner=MagicMock(), run_mode="goal")
    adapter._invoker = MagicMock()
    adapter._invoker.invoke.return_value = capture

    req = _make_request(workspace_path="/tmp/wks")
    result, returned_capture = adapter.execute_and_capture(req)

    assert result is normalized
    assert returned_capture is capture
    adapter._invoker.invoke.assert_called_once_with(prepared)
    assert norm_calls["capture"] is capture
    assert norm_calls["proposal_id"] == req.proposal_id
    assert norm_calls["decision_id"] == req.decision_id
    assert norm_calls["branch_name"] == req.task_branch
    assert norm_calls["workspace_path"] == Path("/tmp/wks")


def test_execute_passes_run_mode_to_mapper(monkeypatch):
    monkeypatch.setattr(adapter_mod, "check_support", lambda req: SupportCheck.yes())
    seen = {}

    def _map(req, run_mode):
        seen["run_mode"] = run_mode
        return MagicMock()

    monkeypatch.setattr(adapter_mod, "map_request", _map)
    monkeypatch.setattr(adapter_mod, "normalize", lambda **kw: MagicMock(spec=ExecutionResult))

    adapter = OpenClawBackendAdapter(runner=MagicMock(), run_mode="fix_pr")
    adapter._invoker = MagicMock()
    adapter._invoker.invoke.return_value = _make_capture()

    adapter.execute(_make_request())

    assert seen["run_mode"] == "fix_pr"


# ---------------------------------------------------------------------------
# build_backend_detail_refs
# ---------------------------------------------------------------------------


def test_build_backend_detail_refs_with_events(tmp_path):
    adapter = OpenClawBackendAdapter(runner=MagicMock())
    req = _make_request(workspace_path=str(tmp_path))
    capture = _make_capture(
        run_id=req.run_id,
        events=[
            {"type": "tool_use", "content": "x" * 200},
            {"type": "message", "summary": "summary-only"},
            {},  # no type/content/summary -> defaults
        ],
    )

    refs = adapter.build_backend_detail_refs(req, capture)

    # one event_trace ref + one structured_result ref
    types = [r.detail_type for r in refs]
    assert "event_trace" in types
    assert "structured_result" in types

    detail_dir = tmp_path / ".operations_center" / "backend_details" / req.run_id
    events_file = detail_dir / "openclaw-events.json"
    capture_file = detail_dir / "openclaw-run-capture.json"
    assert events_file.exists()
    assert capture_file.exists()

    events_data = json.loads(events_file.read_text(encoding="utf-8"))
    assert len(events_data) == 3
    # content truncated to 120 chars
    assert len(events_data[0]["summary"]) == 120
    assert events_data[1]["summary"] == "summary-only"
    # default type for empty event
    assert events_data[2]["event_type"] == "unknown"
    assert events_data[2]["summary"] == ""

    event_ref = next(r for r in refs if r.detail_type == "event_trace")
    assert event_ref.is_required_for_debug is True
    assert event_ref.path == str(events_file)


def test_build_backend_detail_refs_no_events(tmp_path):
    adapter = OpenClawBackendAdapter(runner=MagicMock())
    req = _make_request(workspace_path=str(tmp_path))
    capture = _make_capture(run_id=req.run_id, events=[])

    refs = adapter.build_backend_detail_refs(req, capture)

    # Only the structured_result ref, no event_trace ref.
    assert [r.detail_type for r in refs] == ["structured_result"]
    detail_dir = tmp_path / ".operations_center" / "backend_details" / req.run_id
    assert not (detail_dir / "openclaw-events.json").exists()
    assert (detail_dir / "openclaw-run-capture.json").exists()


def test_structured_ref_required_when_success_and_git_diff(tmp_path):
    adapter = OpenClawBackendAdapter(runner=MagicMock())
    req = _make_request(workspace_path=str(tmp_path))
    capture = _make_capture(
        run_id=req.run_id,
        outcome="success",
        changed_files_source="git_diff",
    )

    refs = adapter.build_backend_detail_refs(req, capture)
    structured = next(r for r in refs if r.detail_type == "structured_result")

    # succeeded and git_diff -> NOT required for debug
    assert structured.is_required_for_debug is False


def test_structured_ref_required_when_failed(tmp_path):
    adapter = OpenClawBackendAdapter(runner=MagicMock())
    req = _make_request(workspace_path=str(tmp_path))
    capture = _make_capture(
        run_id=req.run_id,
        outcome="failure",
        changed_files_source="git_diff",
    )

    refs = adapter.build_backend_detail_refs(req, capture)
    structured = next(r for r in refs if r.detail_type == "structured_result")

    assert structured.is_required_for_debug is True


def test_structured_ref_required_when_source_not_git_diff(tmp_path):
    adapter = OpenClawBackendAdapter(runner=MagicMock())
    req = _make_request(workspace_path=str(tmp_path))
    capture = _make_capture(
        run_id=req.run_id,
        outcome="success",
        changed_files_source="event_stream",
    )

    refs = adapter.build_backend_detail_refs(req, capture)
    structured = next(r for r in refs if r.detail_type == "structured_result")

    assert structured.is_required_for_debug is True


def test_structured_ref_payload_contents(tmp_path):
    adapter = OpenClawBackendAdapter(runner=MagicMock())
    req = _make_request(workspace_path=str(tmp_path))
    capture = _make_capture(
        run_id=req.run_id,
        outcome="partial",
        exit_code=2,
        duration_ms=1234,
        timeout_hit=True,
        changed_files_source="event_stream",
        reported_changed_files=[{"path": "a.py"}],
        output_text="out",
        error_text="err",
    )

    adapter.build_backend_detail_refs(req, capture)

    capture_file = (
        tmp_path
        / ".operations_center"
        / "backend_details"
        / req.run_id
        / "openclaw-run-capture.json"
    )
    payload = json.loads(capture_file.read_text(encoding="utf-8"))
    assert payload["run_id"] == req.run_id
    assert payload["outcome"] == "partial"
    assert payload["exit_code"] == 2
    assert payload["duration_ms"] == 1234
    assert payload["timeout_hit"] is True
    assert payload["changed_files_source"] == "event_stream"
    assert payload["reported_changed_files"] == [{"path": "a.py"}]
    assert payload["output_text"] == "out"
    assert payload["error_text"] == "err"


# ---------------------------------------------------------------------------
# with_stub factory
# ---------------------------------------------------------------------------


def test_with_stub_default_success_runs_end_to_end():
    adapter = OpenClawBackendAdapter.with_stub(
        outcome="success",
        output_text="all good",
    )
    req = _make_request()

    result, capture = adapter.execute_and_capture(req)

    assert capture is not None
    assert capture.outcome == "success"
    assert capture.exit_code == 0
    assert isinstance(result, ExecutionResult)
    assert result.run_id == req.run_id


def test_with_stub_failure_sets_exit_code_one():
    adapter = OpenClawBackendAdapter.with_stub(outcome="failure", error_text="bad")
    req = _make_request()

    result, capture = adapter.execute_and_capture(req)

    assert capture is not None
    assert capture.outcome == "failure"
    assert capture.exit_code == 1
    assert result.success is False


def test_with_stub_passes_events_and_changed_files():
    events = [{"type": "step", "content": "did a thing"}]
    changed = [{"path": "x.py", "change": "modified"}]
    adapter = OpenClawBackendAdapter.with_stub(
        outcome="success",
        events=events,
        reported_changed_files=changed,
    )
    req = _make_request()

    _, capture = adapter.execute_and_capture(req)

    assert capture is not None
    assert capture.events == events
    assert capture.reported_changed_files == changed
    # reported changed files -> event_stream source
    assert capture.changed_files_source == "event_stream"


# ---------------------------------------------------------------------------
# error result builders (direct)
# ---------------------------------------------------------------------------


def test_unsupported_result_builder():
    req = _make_request()
    check = SupportCheck.no("missing X", ["repo_key"])

    result = _unsupported_result(req, check)

    assert result.run_id == req.run_id
    assert result.proposal_id == req.proposal_id
    assert result.decision_id == req.decision_id
    assert result.status == ExecutionStatus.FAILED
    assert result.success is False
    assert result.failure_category == FailureReasonCategory.UNSUPPORTED_REQUEST
    assert "missing X" in result.failure_reason


def test_mapping_error_result_builder():
    req = _make_request()

    result = _mapping_error_result(req, "boom")

    assert result.failure_category == FailureReasonCategory.BACKEND_ERROR
    assert "mapping failed" in result.failure_reason
    assert "boom" in result.failure_reason


def test_invocation_error_result_builder():
    req = _make_request()

    result = _invocation_error_result(req, "kaboom")

    assert result.failure_category == FailureReasonCategory.BACKEND_ERROR
    assert "invocation failed" in result.failure_reason
    assert "kaboom" in result.failure_reason


# ---------------------------------------------------------------------------
# _detail_dir
# ---------------------------------------------------------------------------


def test_detail_dir_creates_nested_directory(tmp_path):
    result = _detail_dir(tmp_path, "run-abc")

    expected = tmp_path / ".operations_center" / "backend_details" / "run-abc"
    assert result == expected
    assert result.is_dir()


def test_detail_dir_idempotent_when_exists(tmp_path):
    first = _detail_dir(tmp_path, "run-xyz")
    # Should not raise on second call (exist_ok=True).
    second = _detail_dir(tmp_path, "run-xyz")

    assert first == second
    assert second.is_dir()
