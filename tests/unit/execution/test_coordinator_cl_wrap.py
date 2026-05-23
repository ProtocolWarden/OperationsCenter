# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the ContextLifecycle hydrate/capture wrap (ADR 0002 P4).

Two layers:

1. **Unit tests for `cl_wrap`** — exercise hydrate/capture call ordering,
   lineage derivation, and the no-op gate without touching the
   coordinator chain. These run independently of the heavy
   ``operations_center.backends`` import graph.

2. **Coordinator integration tests** — wire the wrap through
   ``ExecutionCoordinator.execute()`` end-to-end. These are skipped when
   the coordinator's transitive backend imports are unsatisfiable in the
   current environment (e.g. ``core_runner`` missing).
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

from operations_center.execution import cl_wrap as cl_wrap_module


# ---------------------------------------------------------------------------
# Fake context_lifecycle module
# ---------------------------------------------------------------------------


class _FakeCLState:
    def __init__(self) -> None:
        self.hydrate_calls: list[tuple[str, dict]] = []
        self.capture_calls: list[tuple[str, dict]] = []

    def hydrate(self, lineage_id, work_item):
        self.hydrate_calls.append((lineage_id, work_item))
        return {"lineage_id": lineage_id, "fresh": True}

    def capture(self, lineage_id, result):
        self.capture_calls.append((lineage_id, result))


@pytest.fixture
def fake_cl(monkeypatch):
    """Install a fake `context_lifecycle` module and anchor the env."""
    state = _FakeCLState()
    fake = types.ModuleType("context_lifecycle")
    fake.hydrate = state.hydrate  # type: ignore[attr-defined]
    fake.capture = state.capture  # type: ignore[attr-defined]

    class _AnchorMissingError(Exception):
        pass

    class _SessionNotStartedError(Exception):
        pass

    fake.AnchorMissing = _AnchorMissingError  # type: ignore[attr-defined]
    fake.SessionNotStarted = _SessionNotStartedError  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "context_lifecycle", fake)
    monkeypatch.setenv("CL_ANCHOR", "/tmp/fake-anchor")
    return state


@pytest.fixture
def no_cl_env(monkeypatch):
    monkeypatch.delenv("CL_ANCHOR", raising=False)


# ---------------------------------------------------------------------------
# Wrap unit tests
# ---------------------------------------------------------------------------


def test_wrap_noop_when_anchor_unset(no_cl_env) -> None:
    """No `CL_ANCHOR` → wrap is a strict no-op."""
    work_item = types.SimpleNamespace(run_id="r-1")
    with cl_wrap_module.cl_dispatch_wrap(work_item) as ctx:
        assert ctx["lineage_id"] is None
        ctx["set_result"]({"status": "ok"})


def test_wrap_calls_hydrate_then_capture(fake_cl) -> None:
    work_item = types.SimpleNamespace(run_id="r-abc", repo_key="svc")
    with cl_wrap_module.cl_dispatch_wrap(work_item) as ctx:
        assert ctx["lineage_id"] == "l-r-abc"
        ctx["set_result"]({"status": "ok", "repo_key": "svc"})

    assert len(fake_cl.hydrate_calls) == 1
    h_lineage, h_work = fake_cl.hydrate_calls[0]
    assert h_lineage == "l-r-abc"
    assert h_work["run_id"] == "r-abc"
    assert h_work["repo_key"] == "svc"

    assert len(fake_cl.capture_calls) == 1
    c_lineage, c_result = fake_cl.capture_calls[0]
    assert c_lineage == "l-r-abc"
    assert c_result["status"] == "ok"
    assert c_result["lineage_id"] == "l-r-abc"


def test_wrap_captures_error_on_exception(fake_cl) -> None:
    """Adapter raising mid-dispatch must still leave a CL trace."""
    work_item = types.SimpleNamespace(run_id="r-boom")
    with pytest.raises(RuntimeError, match="boom"):
        with cl_wrap_module.cl_dispatch_wrap(work_item):
            raise RuntimeError("boom")

    assert len(fake_cl.capture_calls) == 1
    lineage, payload = fake_cl.capture_calls[0]
    assert lineage == "l-r-boom"
    assert payload["status"] == "error"
    assert "RuntimeError: boom" in payload["error"]


def test_wrap_captures_no_result_when_setter_not_called(fake_cl) -> None:
    work_item = types.SimpleNamespace(run_id="r-quiet")
    with cl_wrap_module.cl_dispatch_wrap(work_item):
        pass  # caller forgot to call set_result

    assert len(fake_cl.capture_calls) == 1
    _, payload = fake_cl.capture_calls[0]
    assert payload["status"] == "no_result"


def test_wrap_capture_failure_does_not_break_dispatch(fake_cl, monkeypatch) -> None:
    """A buggy CL capture must not propagate out of the wrap."""
    def _boom(_lineage, _result):
        raise RuntimeError("capture write failed")

    monkeypatch.setattr(sys.modules["context_lifecycle"], "capture", _boom)

    work_item = types.SimpleNamespace(run_id="r-x")
    # Must NOT raise — capture failures are logged-and-swallowed.
    with cl_wrap_module.cl_dispatch_wrap(work_item) as ctx:
        ctx["set_result"]({"status": "ok"})


def test_derive_lineage_id_prefers_explicit_lineage() -> None:
    wi = types.SimpleNamespace(lineage_id="l-team-001", run_id="r-x")
    assert cl_wrap_module.derive_lineage_id(wi) == "l-team-001"


def test_derive_lineage_id_falls_back_to_run_id() -> None:
    wi = types.SimpleNamespace(run_id="r-42")
    assert cl_wrap_module.derive_lineage_id(wi) == "l-r-42"


def test_derive_lineage_id_falls_back_to_proposal_id() -> None:
    wi = types.SimpleNamespace(proposal_id="p-7")
    assert cl_wrap_module.derive_lineage_id(wi) == "l-p-7"


def test_derive_lineage_id_unknown() -> None:
    assert cl_wrap_module.derive_lineage_id(object()) == "l-unknown"


def test_derive_lineage_id_preserves_lineage_prefix() -> None:
    """Inputs already prefixed with `l-` aren't double-prefixed."""
    wi = types.SimpleNamespace(run_id="l-team-001")
    assert cl_wrap_module.derive_lineage_id(wi) == "l-team-001"


# ---------------------------------------------------------------------------
# Coordinator integration tests
#
# These exercise the wrap through ExecutionCoordinator.execute(). They
# require the full operations_center.backends import graph to load —
# skipped cleanly when transitive deps are unavailable in this env.
# ---------------------------------------------------------------------------


def _try_import_coordinator():
    try:
        return importlib.import_module(
            "operations_center.execution.coordinator"
        )
    except ImportError as exc:
        pytest.skip(f"coordinator import unavailable in this env: {exc}")


def _build_bundle():
    from operations_center.contracts.enums import BackendName, LaneName
    from operations_center.contracts.routing import LaneDecision
    from operations_center.planning.models import (
        PlanningContext,
        ProposalDecisionBundle,
    )
    from operations_center.planning.proposal_builder import build_proposal

    proposal = build_proposal(
        PlanningContext(
            goal_text="Fix lint failures",
            task_type="lint_fix",
            repo_key="svc",
            clone_url="https://example.invalid/svc.git",
        )
    )
    return ProposalDecisionBundle(
        proposal=proposal,
        decision=LaneDecision(
            proposal_id=proposal.proposal_id,
            selected_lane=LaneName.AIDER_LOCAL,
            selected_backend=BackendName.DIRECT_LOCAL,
        ),
    )


def _success_result(bundle):
    from operations_center.contracts.common import ValidationSummary
    from operations_center.contracts.enums import ExecutionStatus, ValidationStatus
    from operations_center.contracts.execution import ExecutionResult

    return ExecutionResult(
        run_id="run-1",
        proposal_id=bundle.proposal.proposal_id,
        decision_id=bundle.decision.decision_id,
        status=ExecutionStatus.SUCCEEDED,
        success=True,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
    )


def test_coordinator_dispatch_drives_hydrate_and_capture(fake_cl) -> None:
    coord_mod = _try_import_coordinator()
    from operations_center.execution.handoff import ExecutionRuntimeContext
    from operations_center.policy.models import PolicyDecision, PolicyStatus

    class _Policy:
        def evaluate(self, *a, **k):
            return PolicyDecision(status=PolicyStatus.ALLOW)

    class _Adapter:
        def __init__(self, result):
            self.result = result
            self.calls = 0

        def execute(self, request):
            self.calls += 1
            return self.result

    class _Registry:
        def __init__(self, adapter):
            self._a = adapter

        def for_backend(self, _):
            return self._a

    bundle = _build_bundle()
    adapter = _Adapter(_success_result(bundle))
    coordinator = coord_mod.ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_Policy(),
    )
    outcome = coordinator.execute(
        bundle,
        ExecutionRuntimeContext(
            workspace_path=Path("/tmp/workspace"),
            task_branch="auto/lint-fix",
        ),
    )

    assert outcome.executed is True
    assert adapter.calls == 1
    assert len(fake_cl.hydrate_calls) == 1
    assert len(fake_cl.capture_calls) == 1
    # Lineage id is consistent across the pair.
    assert fake_cl.hydrate_calls[0][0] == fake_cl.capture_calls[0][0]
