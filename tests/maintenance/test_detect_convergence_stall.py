# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for the convergence stall breaker."""

from __future__ import annotations

from operations_center.entrypoints.maintenance import detect_convergence_stall as mod
from operations_center.entrypoints.maintenance.detect_convergence_stall import (
    StalledFamily,
    apply_breaker,
    find_stalls,
    main,
    normalize_task,
)

_DESC = "## Provenance\nsource_family: test_visibility\ncandidate_dedup_key: candidate|test_visibility|tv|unknown\n"
_FAIL_COMMENT = "board_worker[improve] failed\n- status: failed\n- category: backend_error\n- reason: non-JSON from agent"


def _issue(tid, *, state="Blocked", desc=_DESC, name="t", labels=None):
    return {
        "id": tid,
        "name": name,
        "state": {"name": state},
        "description": desc,
        "labels": labels or [],
    }


# ── normalize_task ───────────────────────────────────────────────────────────


def test_normalize_extracts_family_dedup_category():
    n = normalize_task(_issue("1"), _FAIL_COMMENT)
    assert n["family"] == "test_visibility"
    assert n["dedup_key"] == "candidate|test_visibility|tv|unknown"
    assert n["failure_category"] == "backend_error"


def test_normalize_family_from_label_fallback():
    issue = _issue(
        "1", desc="no provenance here", labels=[{"name": "source-family: observation_coverage"}]
    )
    assert normalize_task(issue, _FAIL_COMMENT)["family"] == "observation_coverage"


def test_normalize_last_category_wins():
    comments = "category: timeout\n...\ncategory: backend_error"
    assert normalize_task(_issue("1"), comments)["failure_category"] == "backend_error"


# ── find_stalls ──────────────────────────────────────────────────────────────


def _norm(family, category, dedup, tid):
    return {
        "task_id": tid,
        "title": f"t{tid}",
        "family": family,
        "dedup_key": dedup,
        "failure_category": category,
    }


def test_two_backend_errors_same_family_trips_threshold():
    items = [
        _norm("test_visibility", "backend_error", "k1", "1"),
        _norm("test_visibility", "backend_error", "k2", "2"),
    ]
    stalls = find_stalls(items, threshold=2)
    assert len(stalls) == 1
    assert isinstance(stalls[0], StalledFamily)
    assert stalls[0].count == 2
    assert sorted(stalls[0].dedup_keys) == ["k1", "k2"]


def test_below_threshold_not_a_stall():
    items = [_norm("test_visibility", "backend_error", "k1", "1")]
    assert find_stalls(items, threshold=2) == []


def test_genuine_code_failure_category_ignored():
    # A real test/lint failure is the task doing its job — not a convergence stall.
    items = [
        _norm("lint_fix", "test_failure", "k1", "1"),
        _norm("lint_fix", "test_failure", "k2", "2"),
    ]
    assert find_stalls(items, threshold=2) == []


def test_no_family_ignored():
    items = [_norm("", "backend_error", "k1", "1"), _norm("", "backend_error", "k2", "2")]
    assert find_stalls(items, threshold=2) == []


def test_distinct_families_grouped_separately():
    items = [
        _norm("a", "backend_error", "k1", "1"),
        _norm("a", "backend_error", "k2", "2"),
        _norm("b", "backend_error", "k3", "3"),  # only 1 → below threshold
    ]
    stalls = find_stalls(items, threshold=2)
    assert [s.family for s in stalls] == ["a"]


def test_sorted_by_count_desc():
    items = [_norm("hot", "backend_error", f"h{i}", str(i)) for i in range(3)] + [
        _norm("low", "backend_error", f"l{i}", str(10 + i)) for i in range(2)
    ]
    stalls = find_stalls(items, threshold=2)
    assert [s.family for s in stalls] == ["hot", "low"]


# ── apply_breaker ────────────────────────────────────────────────────────────


class _FakeStore:
    def __init__(self, rejected=()):
        self.rejected = set(rejected)
        self.records: list[str] = []

    def is_rejected(self, k):
        return k in self.rejected

    def record_rejection(self, k, *, reason, task_id, task_title="", now=None):
        self.records.append(k)
        self.rejected.add(k)


def test_apply_escalates_once_and_suppresses_each_dedup():
    captures = []
    store = _FakeStore()
    s = StalledFamily(
        "fam", "backend_error", 2, task_ids=["1", "2"], dedup_keys=["k1", "k2"], titles=["t1", "t2"]
    )
    res = apply_breaker([s], store=store, capture=lambda f, c, n: captures.append((f, c, n)))
    assert res == {"escalated": 1, "suppressed": 2}
    assert captures == [("fam", "backend_error", 2)]
    assert sorted(store.records) == ["k1", "k2"]


def test_apply_skips_already_rejected_dedup():
    store = _FakeStore(rejected=["k1"])
    s = StalledFamily(
        "fam", "backend_error", 2, task_ids=["1"], dedup_keys=["k1", "k2"], titles=["t1", "t2"]
    )
    res = apply_breaker([s], store=store, capture=lambda *a: None)
    assert res["suppressed"] == 1  # only k2 newly suppressed
    assert store.records == ["k2"]


def test_apply_empty_is_noop():
    store = _FakeStore()
    res = apply_breaker([], store=store, capture=lambda *a: None)
    assert res == {"escalated": 0, "suppressed": 0}
    assert store.records == []


# ── main ─────────────────────────────────────────────────────────────────────


def test_main_report_only(monkeypatch, capsys):
    monkeypatch.setattr(mod, "load_settings", lambda _c: object())
    monkeypatch.setattr(
        mod, "scan", lambda *a, **k: [StalledFamily("fam", "backend_error", 3, dedup_keys=["k1"])]
    )
    applied = {"called": False}
    monkeypatch.setattr(
        mod, "apply_breaker", lambda *a, **k: applied.__setitem__("called", True) or {}
    )
    rc = main(["--config", "x.yaml"])
    assert rc == 0
    assert "WOULD BREAK" in capsys.readouterr().out
    assert applied["called"] is False


def test_main_apply(monkeypatch, capsys):
    monkeypatch.setattr(mod, "load_settings", lambda _c: object())
    monkeypatch.setattr(
        mod, "scan", lambda *a, **k: [StalledFamily("fam", "backend_error", 3, dedup_keys=["k1"])]
    )
    monkeypatch.setattr(mod, "apply_breaker", lambda *a, **k: {"escalated": 1, "suppressed": 1})
    rc = main(["--config", "x.yaml", "--apply"])
    assert rc == 0
    assert "BROKE" in capsys.readouterr().out
