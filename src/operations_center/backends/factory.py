# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
backends/factory.py — canonical backend adapter registry.

The registry resolves a routed backend name to a canonical adapter that accepts
ExecutionRequest and returns ExecutionResult.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Mapping, Protocol

if TYPE_CHECKING:
    from operations_center.backends.openclaw.invoke import OpenClawRunner

from operations_center.config.settings import Settings
from operations_center.contracts.enums import BackendName
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult

from .aider_local import AiderLocalBackendAdapter
from .critique_executor import CritiqueExecutorBackendAdapter
from .dag_executor import DAGExecutorBackendAdapter
from .direct_local import DirectLocalBackendAdapter
from .openclaw import OpenClawBackendAdapter
from .team_executor import TeamExecutorBackendAdapter


class CanonicalBackendAdapter(Protocol):
    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        ...


class UnsupportedBackendError(RuntimeError):
    """Raised when the runtime has no configured canonical adapter for a backend."""


class CanonicalBackendRegistry:
    """Maps canonical backend names to canonical adapters."""

    def __init__(self, adapters: Mapping[BackendName, CanonicalBackendAdapter]) -> None:
        self._adapters = dict(adapters)

    def for_backend(self, backend: BackendName) -> CanonicalBackendAdapter:
        adapter = self._adapters.get(backend)
        if adapter is None:
            raise UnsupportedBackendError(
                f"No canonical adapter configured for backend '{backend.value}'."
            )
        return adapter

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        openclaw_runner: "OpenClawRunner | None" = None,
    ) -> "CanonicalBackendRegistry":
        adapters: dict[BackendName, CanonicalBackendAdapter] = {
            BackendName.DIRECT_LOCAL: DirectLocalBackendAdapter(
                settings.aider,
            ),
            BackendName.AIDER_LOCAL: AiderLocalBackendAdapter(
                settings.aider_local,
            ),
            BackendName.TEAM_EXECUTOR: TeamExecutorBackendAdapter(
                settings.team_executor,
            ),
            BackendName.DAG_EXECUTOR: DAGExecutorBackendAdapter(
                settings.dag_executor,
            ),
            BackendName.CRITIQUE_EXECUTOR: CritiqueExecutorBackendAdapter(
                settings.critique_executor,
            ),
        }
        if openclaw_runner is not None:
            adapters[BackendName.OPENCLAW] = OpenClawBackendAdapter(
                runner=openclaw_runner,
            )
        return cls(adapters)
