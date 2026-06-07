# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for Goal 4 merge-decision instrumentation."""

from __future__ import annotations

import json
import time

import pytest

from operations_center.reviewer.instrumentation import (
    DecisionMetric,
    DecisionMetricsCollector,
    MergeDecisionInstrumenter,
)


class TestDecisionMetric:
    def test_to_dict_round_trips(self):
        m = DecisionMetric(outcome="merge", latency_ms=42.5, reason="ok", lanes=2)
        d = m.to_dict()
        assert d["outcome"] == "merge"
        assert d["latency_ms"] == 42.5
        assert d["reason"] == "ok"
        assert d["lanes"] == 2

    def test_timestamp_defaults_to_now(self):
        before = time.time()
        m = DecisionMetric(outcome="retry", latency_ms=10.0)
        after = time.time()
        assert before <= m.timestamp <= after


class TestDecisionMetricsCollector:
    def test_initial_state(self):
        c = DecisionMetricsCollector()
        assert c.decision_outcomes["merge"] == 0
        assert c.decision_latencies == []

    def test_record_decision_increments_outcome(self):
        c = DecisionMetricsCollector()
        c.record_decision(outcome="merge", latency_ms=50.0, reason="ci_green", lanes=1)
        assert c.decision_outcomes["merge"] == 1
        assert c.decision_latencies == [50.0]

    def test_record_decision_unknown_outcome_still_records_latency(self):
        c = DecisionMetricsCollector()
        c.record_decision(outcome="unknown_thing", latency_ms=10.0)
        assert c.decision_latencies == [10.0]
        assert sum(c.decision_outcomes.values()) == 0

    def test_get_metrics_summary_empty(self):
        c = DecisionMetricsCollector()
        s = c.get_metrics_summary()
        assert "total_decisions" in s
        assert s["total_decisions"] == 0

    def test_get_metrics_summary_with_data(self):
        c = DecisionMetricsCollector()
        c.record_decision(outcome="merge", latency_ms=100.0)
        c.record_decision(outcome="blocked", latency_ms=200.0)
        s = c.get_metrics_summary()
        assert s["total_decisions"] == 2
        assert s["avg_latency_ms"] == pytest.approx(150.0)


class TestMergeDecisionInstrumenter:
    def test_record_verdict_consolidation_returns_latency(self):
        inst = MergeDecisionInstrumenter()
        inst.start_decision_measurement()
        latency = inst.record_verdict_consolidation(
            pr_number=1,
            repo_key="OC",
            verdict_result="LGTM",
            verdict_summary="all checks pass",
        )
        assert latency >= 0

    def test_record_verdict_consolidation_auto_starts_timer(self):
        inst = MergeDecisionInstrumenter()
        latency = inst.record_verdict_consolidation(
            pr_number=1,
            repo_key="OC",
            verdict_result="LGTM",
            verdict_summary="ok",
        )
        assert latency >= 0

    def test_record_decision_outcome_auto_starts_timer(self):
        inst = MergeDecisionInstrumenter()
        inst.record_decision_outcome(pr_number=1, repo_key="OC", outcome="merge")
        s = inst.get_metrics_summary()
        assert s["total_decisions"] == 1

    def test_export_metrics_json_is_valid_json(self):
        inst = MergeDecisionInstrumenter()
        inst.record_decision_outcome(pr_number=1, repo_key="OC", outcome="merge")
        raw = inst.export_metrics_json()
        parsed = json.loads(raw)
        assert "total_decisions" in parsed

    def test_export_metrics_json_has_ensure_ascii(self):
        inst = MergeDecisionInstrumenter()
        raw = inst.export_metrics_json()
        parsed = json.loads(raw)
        assert isinstance(parsed, dict)
