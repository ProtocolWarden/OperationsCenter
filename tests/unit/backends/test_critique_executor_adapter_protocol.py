# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Protocol-compliance tests for CritiqueExecutorBackendAdapter.

Validates that all code paths through the adapter produce valid ExecutionResult
objects that satisfy the CanonicalBackendAdapter protocol contract.

Coverage:
- 6 key execution paths (happy, import error, exception, backend unavailable, RxP failure, quota events)
- 10 core protocol invariants for each path
- Boundary cases (request ID propagation, validation summary, success/status consistency)
- Edge cases (minimal request, large payload)
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

from cxrp.contracts.runtime_binding import RuntimeBinding
from cxrp.vocabulary.runtime import RuntimeKind, SelectionMode

from operations_center.backends.critique_executor.adapter import CritiqueExecutorBackendAdapter
from operations_center.config.settings import CritiqueExecutorSettings
from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import ExecutionStatus, ValidationStatus
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def _request():
    """Factory for ExecutionRequest with configurable fields."""

    def factory(
        run_id: str = "run_test",
        goal_text: str = "Test goal",
        workspace_path: str | Path = "/tmp/ws",
        model: str = "claude-3-sonnet",
        proposal_id: str = "proposal_default",
        decision_id: str = "decision_default",
        task_branch: str = "branch/default",
    ) -> ExecutionRequest:
        return ExecutionRequest(
            run_id=run_id,
            proposal_id=proposal_id,
            decision_id=decision_id,
            goal_text=goal_text,
            repo_key="TestRepo",
            clone_url="git@github.com:test/repo.git",
            base_branch="main",
            task_branch=task_branch,
            workspace_path=Path(workspace_path),
            runtime_binding=RuntimeBinding(
                kind=RuntimeKind.CLI_SUBSCRIPTION,
                selection_mode=SelectionMode.POLICY_SELECTED,
                model=model,
                provider="anthropic",
                config_ref="test:config",
            ),
        )

    return factory


@pytest.fixture
def _usage_store():
    """Factory for UsageStore stub with configurable capacity."""

    def factory(
        remaining_budget: int = 1000,
        cooldown_backends: dict[str, bool] | None = None,
    ) -> SimpleNamespace:
        if cooldown_backends is None:
            cooldown_backends = {}
        store = SimpleNamespace(
            remaining_exec_capacity=lambda *, now: remaining_budget,
            worker_backend_cooldown_until=lambda backend, *, now: (
                now + timedelta(hours=1) if cooldown_backends.get(backend, False) else None
            ),
            record_quota_event=lambda **kwargs: None,
        )
        return store

    return factory


@pytest.fixture
def fake_critique_modules(monkeypatch):
    """Monkeypatch sys.modules with fake CritiqueExecutor modules."""

    def factory(
        fail_import: bool = False,
        executor_exception: Exception | None = None,
        rxp_payload: dict | None = None,
        runner_class: type | None = None,
    ):
        if fail_import:
            monkeypatch.setitem(sys.modules, "critique_executor.executor", ImportError("Module not found"))
            monkeypatch.setitem(sys.modules, "critique_executor.models", ImportError("Module not found"))
            return

        # Default success payload
        default_payload = {
            "status": "succeeded",
            "error_summary": None,
        }

        if runner_class is not None:
            fake_runner_cls = runner_class
        else:

            class DefaultFakeRunner:
                def __init__(self, topology: str, config, worker_backend: str, working_dir: str) -> None:
                    self.topology = topology
                    self.config = config
                    self.worker_backend = worker_backend
                    self.working_dir = working_dir

                def run(self, goal_text: str, max_rounds: int | None = None):
                    if executor_exception is not None:
                        raise executor_exception
                    return SimpleNamespace(**(rxp_payload or default_payload))

            fake_runner_cls = DefaultFakeRunner

        class FakeCritiqueTopology:
            def __call__(self, value: str):
                return value

        class FakeCritiqueConfig:
            def __init__(self, **kwargs) -> None:
                for k, v in kwargs.items():
                    setattr(self, k, v)

        fake_executor = SimpleNamespace(CritiqueExecutorRunner=fake_runner_cls)
        fake_models = SimpleNamespace(
            CritiqueConfig=FakeCritiqueConfig,
            CritiqueTopology=FakeCritiqueTopology(),
        )
        monkeypatch.setitem(sys.modules, "critique_executor.executor", fake_executor)
        monkeypatch.setitem(sys.modules, "critique_executor.models", fake_models)

    return factory


# ============================================================================
# Assertion Helpers
# ============================================================================


def _assert_protocol_invariants(
    result: ExecutionResult,
    request: ExecutionRequest,
    check_validation_status: bool = True,
) -> None:
    """Assert all 10 protocol invariants hold for the result.

    Args:
        result: The ExecutionResult to validate
        request: The original ExecutionRequest
        check_validation_status: Whether to check validation.status == SKIPPED
                                (False for edge cases where this may vary)
    """
    # I2: Method signature — Result type is ExecutionResult
    assert isinstance(result, ExecutionResult), "I2: Wrong return type"

    # I4: Output contract completeness — All required fields present
    assert hasattr(result, "run_id"), "I4: Missing run_id"
    assert hasattr(result, "success"), "I4: Missing success"
    assert hasattr(result, "status"), "I4: Missing status"
    assert hasattr(result, "failure_reason"), "I4: Missing failure_reason"
    assert hasattr(result, "branch_pushed"), "I4: Missing branch_pushed"
    assert hasattr(result, "validation"), "I4: Missing validation"

    # I7: Request ID preservation
    assert result.run_id == request.run_id, f"I7: run_id mismatch: {result.run_id} != {request.run_id}"
    assert result.proposal_id == request.proposal_id, f"I7: proposal_id mismatch: {result.proposal_id} != {request.proposal_id}"
    assert result.decision_id == request.decision_id, f"I7: decision_id mismatch: {result.decision_id} != {request.decision_id}"
    assert result.branch_name == request.task_branch, f"I7: branch_name mismatch: {result.branch_name} != {request.task_branch}"

    # I10: Immutable contract fields
    assert result.branch_pushed is False, "I10: branch_pushed must be False"

    # I9: Validation summary never None
    assert result.validation is not None, "I9: validation must not be None"
    assert isinstance(result.validation, ValidationSummary), "I9: validation must be ValidationSummary"

    if check_validation_status:
        assert result.validation.status == ValidationStatus.SKIPPED, "I10: validation.status must be SKIPPED"

    # I8: Success invariant — success == (status == SUCCEEDED)
    expected_success = result.status == ExecutionStatus.SUCCEEDED
    assert result.success == expected_success, (
        f"I8: success/status invariant violated: "
        f"success={result.success} but status={result.status}"
    )

    # I4: Failure reason consistency
    if not result.success:
        assert result.failure_reason is not None, "I4: failure_reason required on failure"
    else:
        assert result.failure_reason is None, "I4: failure_reason must be None on success"


def _assert_no_side_effects(request_before: ExecutionRequest, request_after: ExecutionRequest) -> None:
    """Assert adapter introduced no mutations to the request."""
    assert request_before.run_id == request_after.run_id
    assert request_before.proposal_id == request_after.proposal_id
    assert request_before.decision_id == request_after.decision_id
    assert request_before.goal_text == request_after.goal_text
    assert request_before.task_branch == request_after.task_branch


# ============================================================================
# Path P1: Happy Path (Success)
# ============================================================================


def test_protocol_happy_path_success(_request, _usage_store, fake_critique_modules) -> None:
    """P1: Happy path — Valid request → ExecutionResult with success=True."""
    fake_critique_modules(
        rxp_payload={
            "status": "succeeded",
            "error_summary": None,
        }
    )

    request = _request(
        run_id="run_123",
        proposal_id="prop_456",
        decision_id="dec_789",
        task_branch="feat/test",
    )
    adapter = CritiqueExecutorBackendAdapter(
        CritiqueExecutorSettings(worker_backend="claude_code", topology="reflexion"),
        usage_store=_usage_store(remaining_budget=1000),
    )

    result = adapter.execute(request)

    # Protocol invariants
    _assert_protocol_invariants(result, request)

    # Path-specific assertions
    assert result.success is True, "P1: Expected success=True"
    assert result.status == ExecutionStatus.SUCCEEDED, "P1: Expected status=SUCCEEDED"
    assert result.failure_reason is None, "P1: Expected failure_reason=None"


def test_protocol_happy_path_executor_failure(_request, _usage_store, fake_critique_modules) -> None:
    """P1: Happy path with executor failure — RxP failure payload → failure result."""
    fake_critique_modules(
        rxp_payload={
            "status": "failed",
            "error_summary": "Goal failed: insufficient context",
        }
    )

    request = _request(
        run_id="run_456",
        proposal_id="prop_789",
        decision_id="dec_012",
    )
    adapter = CritiqueExecutorBackendAdapter(
        CritiqueExecutorSettings(worker_backend="claude_code", topology="reflexion"),
        usage_store=_usage_store(remaining_budget=1000),
    )

    result = adapter.execute(request)

    # Protocol invariants
    _assert_protocol_invariants(result, request)

    # Path-specific assertions
    assert result.success is False, "P1: Expected success=False"
    assert result.status == ExecutionStatus.FAILED, "P1: Expected status=FAILED"
    assert "insufficient context" in result.failure_reason, "P1: Expected error summary in failure_reason"


# ============================================================================
# Path P2: Import Error (Graceful Degradation)
# ============================================================================


def test_protocol_import_error_graceful_degradation(_request, _usage_store, fake_critique_modules) -> None:
    """P2: Import error — Missing CritiqueExecutor modules → failure result."""
    fake_critique_modules(fail_import=True)

    request = _request(run_id="run_import_error")
    adapter = CritiqueExecutorBackendAdapter(
        CritiqueExecutorSettings(worker_backend="claude_code", topology="reflexion"),
        usage_store=_usage_store(remaining_budget=1000),
    )

    result = adapter.execute(request)

    # Protocol invariants
    _assert_protocol_invariants(result, request)

    # Path-specific assertions
    assert result.success is False, "P2: Expected success=False"
    assert result.status == ExecutionStatus.FAILED, "P2: Expected status=FAILED"
    assert "import" in result.failure_reason.lower() or "not installed" in result.failure_reason.lower(), (
        f"P2: Expected import error in failure_reason, got: {result.failure_reason}"
    )


# ============================================================================
# Path P3: Executor Exception
# ============================================================================


def test_protocol_executor_exception_caught(_request, _usage_store, fake_critique_modules) -> None:
    """P3: Executor exception — Unhandled exception → failure result."""
    executor_exception = RuntimeError("Model rate limit exceeded")
    fake_critique_modules(executor_exception=executor_exception)

    request = _request(run_id="run_exception")
    adapter = CritiqueExecutorBackendAdapter(
        CritiqueExecutorSettings(worker_backend="claude_code", topology="reflexion"),
        usage_store=_usage_store(remaining_budget=1000),
    )

    result = adapter.execute(request)

    # Protocol invariants
    _assert_protocol_invariants(result, request)

    # Path-specific assertions
    assert result.success is False, "P3: Expected success=False"
    assert result.status == ExecutionStatus.FAILED, "P3: Expected status=FAILED"
    assert "rate limit" in result.failure_reason.lower(), (
        f"P3: Expected exception message in failure_reason, got: {result.failure_reason}"
    )


# ============================================================================
# Path P4: Worker Backend Unavailable
# ============================================================================


def test_protocol_worker_backend_unavailable(_request, _usage_store, fake_critique_modules) -> None:
    """P4: Backend unavailable — No backends available → failure result."""

    class FailingRoundRobinRunner:
        """Runner that simulates round-robin exhaustion."""

        def __init__(self, topology: str, config, worker_backend: str, working_dir: str) -> None:
            pass

        def run(self, goal_text: str, max_rounds: int | None = None):
            raise RuntimeError("This should not be called in this test")

    fake_critique_modules(runner_class=FailingRoundRobinRunner)

    request = _request(run_id="run_backend_unavailable")
    # Use a usage store with no remaining capacity to trigger backend unavailable
    adapter = CritiqueExecutorBackendAdapter(
        CritiqueExecutorSettings(worker_backend="claude_code", topology="reflexion"),
        usage_store=_usage_store(
            remaining_budget=0,
            cooldown_backends={"claude_code": True, "codex_cli": True},
        ),
    )

    result = adapter.execute(request)

    # Protocol invariants
    _assert_protocol_invariants(result, request)

    # Path-specific assertions
    assert result.success is False, "P4: Expected success=False"
    assert result.status == ExecutionStatus.FAILED, "P4: Expected status=FAILED"
    assert "backend" in result.failure_reason.lower() or "unavailable" in result.failure_reason.lower(), (
        f"P4: Expected backend error in failure_reason, got: {result.failure_reason}"
    )


# ============================================================================
# Path P5: RxP Payload Indicates Failure (with specific error details)
# ============================================================================


def test_protocol_rxp_failure_payload_extraction(_request, _usage_store, fake_critique_modules) -> None:
    """P5: RxP failure payload — Executor failed with details → failure result."""
    fake_critique_modules(
        rxp_payload={
            "status": "failed",
            "error_summary": "Goal execution timed out after 30s",
        }
    )

    request = _request(run_id="run_timeout")
    adapter = CritiqueExecutorBackendAdapter(
        CritiqueExecutorSettings(worker_backend="claude_code", topology="reflexion"),
        usage_store=_usage_store(remaining_budget=1000),
    )

    result = adapter.execute(request)

    # Protocol invariants
    _assert_protocol_invariants(result, request)

    # Path-specific assertions
    assert result.success is False, "P5: Expected success=False"
    assert result.status == ExecutionStatus.FAILED, "P5: Expected status=FAILED"
    assert "timed out" in result.failure_reason.lower(), (
        f"P5: Expected timeout message in failure_reason, got: {result.failure_reason}"
    )


# ============================================================================
# Path P6: Quota Event Recording (Rate-Limit Failure)
# ============================================================================


def test_protocol_quota_event_recording_on_rate_limit(_request, _usage_store, fake_critique_modules) -> None:
    """P6: Quota event — Rate-limit failure → quota event recorded."""

    quota_events_recorded = []

    def mock_record_quota_event(**kwargs):
        quota_events_recorded.append(kwargs)

    fake_critique_modules(
        rxp_payload={
            "status": "failed",
            "error_summary": "Rate limit: 429 Too Many Requests",
        }
    )

    request = _request(run_id="run_rate_limited")
    usage_store = _usage_store(remaining_budget=0)
    usage_store.record_quota_event = mock_record_quota_event

    adapter = CritiqueExecutorBackendAdapter(
        CritiqueExecutorSettings(worker_backend="claude_code", topology="reflexion"),
        usage_store=usage_store,
    )

    result = adapter.execute(request)

    # Protocol invariants
    _assert_protocol_invariants(result, request)

    # Path-specific assertions
    assert result.success is False, "P6: Expected success=False"
    assert result.status == ExecutionStatus.FAILED, "P6: Expected status=FAILED"
    assert "limit" in result.failure_reason.lower(), (
        f"P6: Expected rate limit message in failure_reason, got: {result.failure_reason}"
    )
    # Quota event should have been recorded
    assert len(quota_events_recorded) > 0, "P6: Expected quota event to be recorded"
    assert quota_events_recorded[0]["task_id"] == "run_rate_limited"


# ============================================================================
# Boundary Invariant: Request ID Propagation
# ============================================================================


@pytest.mark.parametrize(
    "request_fields,description",
    [
        (
            {
                "run_id": "r1",
                "proposal_id": "p1",
                "decision_id": "d1",
                "task_branch": "branch/test1",
            },
            "minimal",
        ),
        (
            {
                "run_id": "r2",
                "proposal_id": "p2",
                "decision_id": "d2",
                "task_branch": "branch/test2",
            },
            "full",
        ),
        (
            {
                "run_id": "x" * 100,
                "proposal_id": "y" * 100,
                "decision_id": "z" * 100,
                "task_branch": "b" * 100,
            },
            "large",
        ),
    ],
    ids=["minimal", "full", "large"],
)
def test_protocol_request_id_propagation(_request, _usage_store, fake_critique_modules, request_fields, description) -> None:
    """Boundary: Request IDs preserved exactly across different patterns."""
    fake_critique_modules(
        rxp_payload={
            "status": "succeeded",
            "error_summary": None,
        }
    )

    request = _request(**request_fields)
    adapter = CritiqueExecutorBackendAdapter(
        CritiqueExecutorSettings(worker_backend="claude_code", topology="reflexion"),
        usage_store=_usage_store(remaining_budget=1000),
    )

    result = adapter.execute(request)

    # Protocol invariants (including ID propagation)
    _assert_protocol_invariants(result, request)

    # Explicit ID checks with no truncation/coercion
    assert result.run_id == request.run_id, f"ID propagation ({description}): run_id mismatch"
    assert result.proposal_id == request.proposal_id, f"ID propagation ({description}): proposal_id mismatch"
    assert result.decision_id == request.decision_id, f"ID propagation ({description}): decision_id mismatch"
    assert result.branch_name == request.task_branch, f"ID propagation ({description}): branch_name mismatch"


# ============================================================================
# Boundary Invariant: Validation Summary Completeness
# ============================================================================


@pytest.mark.parametrize(
    "scenario_name,executor_exception,rxp_payload",
    [
        ("success_path", None, {"status": "succeeded", "error_summary": None}),
        ("executor_exception_path", RuntimeError("Test error"), None),
        ("failure_payload_path", None, {"status": "failed", "error_summary": "Test failure"}),
    ],
    ids=["success", "exception", "failure_payload"],
)
def test_protocol_validation_summary_never_none(
    _request,
    _usage_store,
    fake_critique_modules,
    scenario_name,
    executor_exception,
    rxp_payload,
) -> None:
    """Boundary: ValidationSummary always present and well-formed, never None."""
    fake_critique_modules(
        executor_exception=executor_exception,
        rxp_payload=rxp_payload,
    )

    request = _request(run_id=f"run_{scenario_name}")
    adapter = CritiqueExecutorBackendAdapter(
        CritiqueExecutorSettings(worker_backend="claude_code", topology="reflexion"),
        usage_store=_usage_store(remaining_budget=1000),
    )

    result = adapter.execute(request)

    # Critical assertion: validation must never be None
    assert result.validation is not None, f"{scenario_name}: validation must not be None"
    assert isinstance(result.validation, ValidationSummary), f"{scenario_name}: validation must be ValidationSummary"
    assert result.validation.status == ValidationStatus.SKIPPED, (
        f"{scenario_name}: validation.status must always be SKIPPED for CritiqueExecutor"
    )

    # Verify it has required fields
    assert hasattr(result.validation, "status"), f"{scenario_name}: validation missing status field"


# ============================================================================
# Boundary Invariant: Success/Status Consistency
# ============================================================================


@pytest.mark.parametrize(
    "scenario_name,executor_exception,rxp_payload",
    [
        ("success_case", None, {"status": "succeeded", "error_summary": None}),
        ("failure_case", None, {"status": "failed", "error_summary": "Test failure"}),
        ("exception_case", RuntimeError("Test error"), None),
    ],
    ids=["success", "failure", "exception"],
)
def test_protocol_success_status_invariant(
    _request,
    _usage_store,
    fake_critique_modules,
    scenario_name,
    executor_exception,
    rxp_payload,
) -> None:
    """Boundary: Invariant success == (status == SUCCEEDED) holds across all paths."""
    fake_critique_modules(
        executor_exception=executor_exception,
        rxp_payload=rxp_payload,
    )

    request = _request(run_id=f"run_{scenario_name}")
    adapter = CritiqueExecutorBackendAdapter(
        CritiqueExecutorSettings(worker_backend="claude_code", topology="reflexion"),
        usage_store=_usage_store(remaining_budget=1000),
    )

    result = adapter.execute(request)

    # Critical invariant: success == (status == SUCCEEDED)
    expected_success = result.status == ExecutionStatus.SUCCEEDED
    assert result.success == expected_success, (
        f"{scenario_name}: invariant violated: "
        f"success={result.success} but status={result.status}"
    )

    # Explicit validation
    if result.status == ExecutionStatus.SUCCEEDED:
        assert result.success is True, f"{scenario_name}: SUCCEEDED implies success=True"
    else:
        assert result.success is False, f"{scenario_name}: non-SUCCEEDED implies success=False"


# ============================================================================
# Edge Case: Minimal Request
# ============================================================================


def test_protocol_minimal_request(_request, _usage_store, fake_critique_modules) -> None:
    """Edge: Minimal valid request — Sparse field values → valid result."""
    fake_critique_modules(
        rxp_payload={
            "status": "succeeded",
            "error_summary": None,
        }
    )

    request = _request(
        run_id="run_minimal",
        goal_text="",
        proposal_id="p_minimal",
        decision_id="d_minimal",
        task_branch="branch/minimal",
    )
    adapter = CritiqueExecutorBackendAdapter(
        CritiqueExecutorSettings(worker_backend="claude_code", topology="reflexion"),
        usage_store=_usage_store(remaining_budget=1000),
    )

    result = adapter.execute(request)

    # Protocol invariants
    _assert_protocol_invariants(result, request)

    # Verify minimal fields are preserved correctly
    assert result.run_id == "run_minimal"
    assert result.proposal_id == "p_minimal"
    assert result.decision_id == "d_minimal"
    assert result.branch_name == "branch/minimal"


# ============================================================================
# Edge Case: Large Request Payload
# ============================================================================


def test_protocol_large_request_payload(_request, _usage_store, fake_critique_modules) -> None:
    """Edge: Large payload — 100KB goal_text, deep paths → no truncation."""
    fake_critique_modules(
        rxp_payload={
            "status": "succeeded",
            "error_summary": None,
        }
    )

    large_goal = "x" * 100_000
    large_path = "/very/long/" + "/".join(["subdir"] * 100)
    large_id = "x" * 256

    request = _request(
        run_id=large_id,
        goal_text=large_goal,
        workspace_path=large_path,
        proposal_id=large_id,
        decision_id=large_id,
        task_branch=large_id,
    )
    adapter = CritiqueExecutorBackendAdapter(
        CritiqueExecutorSettings(worker_backend="claude_code", topology="reflexion"),
        usage_store=_usage_store(remaining_budget=1000),
    )

    result = adapter.execute(request)

    # Protocol invariants
    _assert_protocol_invariants(result, request)

    # Verify no truncation of large IDs
    assert result.run_id == large_id, "Edge: run_id should not be truncated"
    assert result.proposal_id == large_id, "Edge: proposal_id should not be truncated"
    assert result.decision_id == large_id, "Edge: decision_id should not be truncated"
    assert result.branch_name == large_id, "Edge: branch_name should not be truncated"


# ============================================================================
# Execute and Capture: Observability Integration
# ============================================================================


def test_protocol_execute_and_capture_returns_observability(_request, _usage_store, fake_critique_modules) -> None:
    """Capture: execute_and_capture returns result + observability snapshot."""
    fake_critique_modules(
        rxp_payload={
            "status": "succeeded",
            "error_summary": None,
        }
    )

    request = _request(run_id="run_with_capture")
    adapter = CritiqueExecutorBackendAdapter(
        CritiqueExecutorSettings(worker_backend="claude_code", topology="reflexion"),
        usage_store=_usage_store(remaining_budget=1000),
    )

    result, capture = adapter.execute_and_capture(request)

    # Verify result
    _assert_protocol_invariants(result, request)

    # Verify capture
    assert capture is not None, "Capture: capture object should not be None"
    assert hasattr(capture, "observed_runtime"), "Capture: should have observed_runtime attribute"
    assert isinstance(capture.observed_runtime, dict), "Capture: observed_runtime should be dict"
    assert "selected_worker_backend" in capture.observed_runtime, (
        "Capture: observed_runtime should have selected_worker_backend"
    )


def test_protocol_execute_and_capture_on_error_still_captures(_request, _usage_store, fake_critique_modules) -> None:
    """Capture: execute_and_capture returns capture even on failure."""
    fake_critique_modules(
        rxp_payload={
            "status": "failed",
            "error_summary": "Test failure",
        }
    )

    request = _request(run_id="run_capture_on_failure")
    adapter = CritiqueExecutorBackendAdapter(
        CritiqueExecutorSettings(worker_backend="claude_code", topology="reflexion"),
        usage_store=_usage_store(remaining_budget=1000),
    )

    result, capture = adapter.execute_and_capture(request)

    # Verify result is failure
    assert result.success is False, "Capture on error: result should be failure"

    # Verify capture is still present
    assert capture is not None, "Capture on error: capture object should not be None"
    assert isinstance(capture.observed_runtime, dict), "Capture on error: observed_runtime should be dict"
