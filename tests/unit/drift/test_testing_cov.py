# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from unittest.mock import patch

import pytest

from operations_center.drift.engine import BackendDriftFinding, DriftKind
from operations_center.drift.testing import DriftInjectionFixture


def test_default_request_id() -> None:
    fix = DriftInjectionFixture(backend_id="be")
    assert fix.backend_id == "be"
    assert fix.request_id == "test-request"


def test_custom_request_id() -> None:
    fix = DriftInjectionFixture(backend_id="be", request_id="req-9")
    assert fix.request_id == "req-9"


def test_inject_runtime_drift_detected() -> None:
    fix = DriftInjectionFixture(backend_id="team_executor", request_id="req-1")
    finding = fix.inject_runtime(
        bound={"kind": "cli_subscription", "model": "opus"},
        observed={"kind": "cli_subscription", "model": "sonnet"},
    )
    assert finding is not None
    assert finding.drift_type is DriftKind.RUNTIME
    assert finding.drift_type.value == "runtime"
    assert finding.backend_id == "team_executor"
    assert finding.request_id == "req-1"
    assert finding.bound_or_allowed == {"kind": "cli_subscription", "model": "opus"}
    assert finding.observed == {"kind": "cli_subscription", "model": "sonnet"}


def test_inject_runtime_no_drift_returns_none() -> None:
    fix = DriftInjectionFixture(backend_id="be")
    finding = fix.inject_runtime(
        bound={"model": "opus"},
        observed={"model": "opus"},
    )
    assert finding is None


def test_inject_capability_drift_detected() -> None:
    fix = DriftInjectionFixture(backend_id="be", request_id="r2")
    finding = fix.inject_capability(
        allowed={"read"},
        used={"read", "write"},
    )
    assert finding is not None
    assert finding.drift_type is DriftKind.CAPABILITY
    assert finding.observed == {"used": ["read", "write"]}
    assert finding.bound_or_allowed == {"allowed": ["read"]}
    assert "write" in finding.impact


def test_inject_capability_no_drift_returns_none() -> None:
    fix = DriftInjectionFixture(backend_id="be")
    finding = fix.inject_capability(allowed={"read", "write"}, used={"read"})
    assert finding is None


def test_inject_output_shape_drift_detected() -> None:
    fix = DriftInjectionFixture(backend_id="be", request_id="r3")
    finding = fix.inject_output_shape(
        result_payload={"ok": True, "rogue_field": 1},
    )
    assert finding is not None
    assert finding.drift_type is DriftKind.OUTPUT_SHAPE
    assert finding.observed == {"extra_top_level_fields": ["rogue_field"]}
    assert finding.request_id == "r3"


def test_inject_output_shape_no_drift_returns_none() -> None:
    fix = DriftInjectionFixture(backend_id="be")
    finding = fix.inject_output_shape(
        result_payload={"ok": True, "status": "done", "schema_version": "0.2"},
    )
    assert finding is None


def test_inject_internal_routing_drift_detected() -> None:
    fix = DriftInjectionFixture(backend_id="be", request_id="r4")
    finding = fix.inject_internal_routing(
        bound={"planner": "opus"},
        observed={"planner": "sonnet"},
    )
    assert finding is not None
    assert finding.drift_type is DriftKind.INTERNAL_ROUTING
    assert finding.observed == {"planner": "sonnet"}
    assert finding.bound_or_allowed == {"planner": "opus"}


def test_inject_internal_routing_no_drift_returns_none() -> None:
    fix = DriftInjectionFixture(backend_id="be")
    finding = fix.inject_internal_routing(
        bound={"planner": "opus"},
        observed={"planner": "opus"},
    )
    assert finding is None


def test_inject_runtime_forwards_exact_kwargs_to_detector() -> None:
    fix = DriftInjectionFixture(backend_id="bx", request_id="rx")
    sentinel = BackendDriftFinding(
        backend_id="bx",
        request_id="rx",
        drift_type=DriftKind.RUNTIME,
        observed={},
        bound_or_allowed={},
        impact="x",
    )
    with patch(
        "operations_center.drift.testing.detect_runtime_drift",
        return_value=sentinel,
    ) as m:
        result = fix.inject_runtime(bound={"a": 1}, observed={"a": 2})
    assert result is sentinel
    m.assert_called_once_with(
        backend_id="bx",
        request_id="rx",
        bound_runtime={"a": 1},
        observed_runtime={"a": 2},
    )


def test_inject_capability_forwards_exact_kwargs_to_detector() -> None:
    fix = DriftInjectionFixture(backend_id="bx", request_id="rx")
    with patch(
        "operations_center.drift.testing.detect_capability_drift",
        return_value=None,
    ) as m:
        result = fix.inject_capability(allowed={"r"}, used={"w"})
    assert result is None
    m.assert_called_once_with(
        backend_id="bx",
        request_id="rx",
        allowed_capabilities={"r"},
        used_capabilities={"w"},
    )


def test_inject_output_shape_forwards_exact_kwargs_to_detector() -> None:
    fix = DriftInjectionFixture(backend_id="bx", request_id="rx")
    with patch(
        "operations_center.drift.testing.detect_output_shape_drift",
        return_value=None,
    ) as m:
        result = fix.inject_output_shape(result_payload={"ok": True})
    assert result is None
    m.assert_called_once_with(
        backend_id="bx",
        request_id="rx",
        result_payload={"ok": True},
    )


def test_inject_internal_routing_forwards_exact_kwargs_to_detector() -> None:
    fix = DriftInjectionFixture(backend_id="bx", request_id="rx")
    with patch(
        "operations_center.drift.testing.detect_internal_routing_drift",
        return_value=None,
    ) as m:
        result = fix.inject_internal_routing(bound={"a": "x"}, observed={"a": "y"})
    assert result is None
    m.assert_called_once_with(
        backend_id="bx",
        request_id="rx",
        bound_agent_models={"a": "x"},
        observed_agent_models={"a": "y"},
    )


def test_keyword_only_arguments_enforced() -> None:
    fix = DriftInjectionFixture(backend_id="be")
    with pytest.raises(TypeError):
        fix.inject_runtime({"a": 1}, {"a": 2})  # type: ignore[misc]
