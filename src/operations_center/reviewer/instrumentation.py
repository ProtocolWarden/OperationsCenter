# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Goal 4 instrumentation — merge-decision metrics and structured logging.

Provides structured metrics collection for verdict consolidation and merge-decision
outcomes with baseline <500ms latency tracking and anomaly detection support.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DecisionMetric:
    """Single merge decision metric."""

    outcome: str
    latency_ms: float
    reason: str = ""
    lanes: int = 0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome,
            "latency_ms": self.latency_ms,
            "reason": self.reason,
            "lanes": self.lanes,
            "timestamp": self.timestamp,
        }


class DecisionMetricsCollector:
    """Collector for merge decision metrics during decision flow execution."""

    def __init__(self):
        self.decision_outcomes: dict[str, int] = {
            "merge": 0,
            "blocked": 0,
            "retry": 0,
            "escalate": 0,
        }
        self.decision_latencies: list[float] = []
        self.structured_logs: list[dict] = []

    def record_decision(
        self,
        outcome: str,
        latency_ms: float,
        reason: str = "",
        lanes: int = 0,
    ) -> None:
        """Record a merge decision with metrics."""
        if outcome in self.decision_outcomes:
            self.decision_outcomes[outcome] += 1
        self.decision_latencies.append(latency_ms)
        self.structured_logs.append(
            {
                "decision": outcome,
                "latency_ms": latency_ms,
                "reason": reason,
                "lanes": lanes,
                "timestamp": time.time(),
            }
        )
        logger.info(
            "merge_decision_metric",
            extra={
                "outcome": outcome,
                "latency_ms": f"{latency_ms:.2f}",
                "reason": reason,
                "lanes": lanes,
            },
        )

    def get_metrics_summary(self) -> dict:
        """Return aggregated metrics."""
        total_decisions = sum(self.decision_outcomes.values())
        avg_latency = (
            sum(self.decision_latencies) / len(self.decision_latencies)
            if self.decision_latencies
            else 0
        )
        max_latency = max(self.decision_latencies) if self.decision_latencies else 0
        min_latency = min(self.decision_latencies) if self.decision_latencies else 0
        return {
            "total_decisions": total_decisions,
            "outcomes": self.decision_outcomes.copy(),
            "avg_latency_ms": avg_latency,
            "max_latency_ms": max_latency,
            "min_latency_ms": min_latency,
            "decision_log": self.structured_logs,
        }


class MergeDecisionInstrumenter:
    """Instruments merge-decision and verdict-consolidation flows.

    Provides structured logging, metrics collection, and baseline tracking for:
    - Verdict consolidation latency
    - Merge decision outcomes (approved/blocked/retry/escalate counts)
    - Decision latency histogram (baseline <500ms)
    - Anomaly detection triggers (unusual retry rates, CI-green delays)
    """

    def __init__(self):
        self.metrics_collector = DecisionMetricsCollector()
        self._decision_start_time: Optional[float] = None

    def start_decision_measurement(self) -> None:
        """Begin latency measurement for a merge decision."""
        self._decision_start_time = time.time()

    def record_verdict_consolidation(
        self,
        pr_number: int,
        repo_key: str,
        verdict_result: str,
        verdict_summary: str,
    ) -> float:
        """Record verdict consolidation step and return latency in ms."""
        if self._decision_start_time is None:
            self._decision_start_time = time.time()
        latency_ms = (time.time() - self._decision_start_time) * 1000
        logger.info(
            "verdict_consolidation",
            extra={
                "pr_number": pr_number,
                "repo_key": repo_key,
                "result": verdict_result,
                "summary": verdict_summary[:100],
                "latency_ms": f"{latency_ms:.2f}",
            },
        )
        return latency_ms

    def record_decision_outcome(
        self,
        pr_number: int,
        repo_key: str,
        outcome: str,
        reason: str = "",
        lanes: int = 1,
    ) -> None:
        """Record a merge decision outcome (merge/blocked/retry/escalate)."""
        if self._decision_start_time is None:
            self._decision_start_time = time.time()
        latency_ms = (time.time() - self._decision_start_time) * 1000

        # Map decision outcome to metrics categories
        metric_outcome = outcome.lower()
        if metric_outcome not in self.metrics_collector.decision_outcomes:
            metric_outcome = "retry"

        self.metrics_collector.record_decision(
            outcome=metric_outcome,
            latency_ms=latency_ms,
            reason=reason,
            lanes=lanes,
        )

        logger.info(
            "merge_decision_outcome",
            extra={
                "pr_number": pr_number,
                "repo_key": repo_key,
                "outcome": outcome,
                "reason": reason,
                "latency_ms": f"{latency_ms:.2f}",
                "lanes": lanes,
                "baseline_ok": latency_ms < 500,
            },
        )

    def record_ci_gate_defer(
        self,
        pr_number: int,
        repo_key: str,
        wait_cycle: int,
        max_cycles: int,
        failed_checks: list[str],
    ) -> None:
        """Record CI gate deferral with wait cycle tracking."""
        logger.info(
            "ci_gate_defer",
            extra={
                "pr_number": pr_number,
                "repo_key": repo_key,
                "wait_cycle": wait_cycle,
                "max_cycles": max_cycles,
                "failed_checks": len(failed_checks),
                "failed_check_names": failed_checks[:3],
            },
        )

    def record_fix_attempt(
        self,
        pr_number: int,
        repo_key: str,
        attempt: int,
        max_attempts: int,
        concerns: str,
    ) -> None:
        """Record fix attempt for concerns resolution."""
        logger.info(
            "fix_attempt",
            extra={
                "pr_number": pr_number,
                "repo_key": repo_key,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "concerns_length": len(concerns),
            },
        )

    def record_retry_decision(
        self,
        pr_number: int,
        repo_key: str,
        reason: str,
    ) -> None:
        """Record a retry decision (mixed verdicts, CI issues, etc)."""
        self.metrics_collector.record_decision(
            outcome="retry",
            latency_ms=(time.time() - self._decision_start_time) * 1000
            if self._decision_start_time
            else 0,
            reason=reason,
        )
        logger.info(
            "retry_decision",
            extra={
                "pr_number": pr_number,
                "repo_key": repo_key,
                "reason": reason,
            },
        )

    def record_escalation(
        self,
        pr_number: int,
        repo_key: str,
        reason: str,
        detail: str = "",
    ) -> None:
        """Record escalation to human (decision cannot be auto-resolved)."""
        self.metrics_collector.record_decision(
            outcome="escalate",
            latency_ms=(time.time() - self._decision_start_time) * 1000
            if self._decision_start_time
            else 0,
            reason=reason,
        )
        logger.warning(
            "decision_escalation",
            extra={
                "pr_number": pr_number,
                "repo_key": repo_key,
                "reason": reason,
                "detail": detail[:200],
            },
        )

    def get_metrics_summary(self) -> dict:
        """Get aggregated decision metrics."""
        return self.metrics_collector.get_metrics_summary()

    def export_metrics_json(self) -> str:
        """Export metrics as JSON string."""
        summary = self.get_metrics_summary()
        return json.dumps(summary, indent=2, ensure_ascii=False)


# Global instrumenter instance
_global_instrumenter: Optional[MergeDecisionInstrumenter] = None


def get_instrumenter() -> MergeDecisionInstrumenter:
    """Get or create the global merge decision instrumenter."""
    global _global_instrumenter
    if _global_instrumenter is None:
        _global_instrumenter = MergeDecisionInstrumenter()
    return _global_instrumenter


def record_decision_outcome(
    pr_number: int,
    repo_key: str,
    outcome: str,
    reason: str = "",
    lanes: int = 1,
) -> None:
    """Record a merge decision to the global instrumenter."""
    get_instrumenter().record_decision_outcome(
        pr_number=pr_number,
        repo_key=repo_key,
        outcome=outcome,
        reason=reason,
        lanes=lanes,
    )


def record_ci_gate_defer(
    pr_number: int,
    repo_key: str,
    wait_cycle: int,
    max_cycles: int,
    failed_checks: list[str],
) -> None:
    """Record CI gate deferral to the global instrumenter."""
    get_instrumenter().record_ci_gate_defer(
        pr_number=pr_number,
        repo_key=repo_key,
        wait_cycle=wait_cycle,
        max_cycles=max_cycles,
        failed_checks=failed_checks,
    )


def record_escalation(
    pr_number: int,
    repo_key: str,
    reason: str,
    detail: str = "",
) -> None:
    """Record escalation to the global instrumenter."""
    get_instrumenter().record_escalation(
        pr_number=pr_number,
        repo_key=repo_key,
        reason=reason,
        detail=detail,
    )
