# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Wiring tests for execute/main — RuntimeBindingPolicy (#2) + RecoveryPolicy (#3).

These pin the fail-safe-default contract: with the shipped default config the
coordinator is constructed exactly as before (no runtime-binding policy,
single-shot recovery). The activation paths only engage when settings opt in.
"""

from __future__ import annotations

from types import SimpleNamespace

from operations_center.config.settings import RecoverySettings, RuntimeBindingSettings
from operations_center.entrypoints.execute.main import _build_recovery_policy
from operations_center.execution.coordinator import ExecutionCoordinator
from operations_center.policy.runtime_binding_policy import DEFAULT_POLICY, RuntimeBindingPolicy


# ---------------------------------------------------------------------------
# Subsystem #2 — RuntimeBindingPolicy resolution
# ---------------------------------------------------------------------------


class TestResolveRuntimeBindingPolicy:
    def test_disabled_returns_none(self):
        """FAIL-SAFE DEFAULT: runtime_binding disabled → None → static-team behavior."""
        settings = SimpleNamespace(runtime_binding=RuntimeBindingSettings())
        assert ExecutionCoordinator.resolve_runtime_binding_policy(settings) is None

    def test_missing_attr_returns_none(self):
        """A settings object without the attribute at all still fails safe."""
        assert ExecutionCoordinator.resolve_runtime_binding_policy(SimpleNamespace()) is None

    def test_enabled_no_path_uses_default_policy(self):
        settings = SimpleNamespace(runtime_binding=RuntimeBindingSettings(enabled=True))
        policy = ExecutionCoordinator.resolve_runtime_binding_policy(settings)
        assert policy is DEFAULT_POLICY

    def test_enabled_with_path_loads_yaml(self, tmp_path):
        yaml_path = tmp_path / "rbp.yaml"
        yaml_path.write_text(
            "rules:\n"
            "  - name: only\n"
            "    when:\n"
            "      task_type: refactor\n"
            "      lane: claude_cli\n"
            "    bind:\n"
            "      kind: cli_subscription\n"
            "      provider: anthropic\n"
            "      model: haiku\n",
            encoding="utf-8",
        )
        settings = SimpleNamespace(
            runtime_binding=RuntimeBindingSettings(enabled=True, policy_path=yaml_path)
        )
        policy = ExecutionCoordinator.resolve_runtime_binding_policy(settings)
        assert isinstance(policy, RuntimeBindingPolicy)
        assert [r.name for r in policy.rules] == ["only"]
        assert policy.rules[0].model == "haiku"

    def test_enabled_with_missing_path_is_empty_policy_not_crash(self, tmp_path):
        """A typo'd policy_path yields an empty policy (passthrough), not an error."""
        settings = SimpleNamespace(
            runtime_binding=RuntimeBindingSettings(
                enabled=True, policy_path=tmp_path / "does_not_exist.yaml"
            )
        )
        policy = ExecutionCoordinator.resolve_runtime_binding_policy(settings)
        assert isinstance(policy, RuntimeBindingPolicy)
        assert policy.rules == ()
        assert policy.default is None


class TestFromDefaultsWithRuntimePolicy:
    def test_advertised_factory_wires_default_policy(self):
        """The previously-nonexistent advertised constructor now exists and wires
        the bundled DEFAULT_POLICY."""
        registry = SimpleNamespace(for_backend=lambda b: None)
        coord = ExecutionCoordinator.from_defaults_with_runtime_policy(adapter_registry=registry)
        # The policy is private; assert via behavior — selection is active.
        assert coord._runtime_binding_policy is DEFAULT_POLICY

    def test_explicit_policy_overrides_default(self):
        registry = SimpleNamespace(for_backend=lambda b: None)
        empty = RuntimeBindingPolicy(rules=())
        coord = ExecutionCoordinator.from_defaults_with_runtime_policy(
            adapter_registry=registry, runtime_binding_policy=empty
        )
        assert coord._runtime_binding_policy is empty


# ---------------------------------------------------------------------------
# Subsystem #3 — RecoveryPolicy construction from settings
# ---------------------------------------------------------------------------


class TestBuildRecoveryPolicy:
    def test_default_is_single_shot(self):
        """FAIL-SAFE DEFAULT: default RecoverySettings → max_attempts=1 (no retry)."""
        settings = SimpleNamespace(recovery=RecoverySettings())
        policy = _build_recovery_policy(settings)
        assert policy.max_attempts == 1
        assert policy.retry_unknowns is False
        assert policy.unknown_retry_limit == 0

    def test_raised_max_attempts_threaded_through(self):
        settings = SimpleNamespace(
            recovery=RecoverySettings(max_attempts=3, max_delay_seconds=12.5)
        )
        policy = _build_recovery_policy(settings)
        assert policy.max_attempts == 3
        assert policy.max_delay_seconds == 12.5

    def test_retryable_kinds_keep_conservative_defaults(self):
        """Raising max_attempts must not silently widen the retryable failure set."""
        from operations_center.execution.recovery_loop import ExecutionFailureKind

        settings = SimpleNamespace(recovery=RecoverySettings(max_attempts=5))
        policy = _build_recovery_policy(settings)
        # RATE_LIMIT is intentionally NOT retryable-by-default (needs backoff path).
        assert ExecutionFailureKind.RATE_LIMIT not in policy.retryable_kinds
        assert ExecutionFailureKind.AUTH in policy.non_retryable_kinds
