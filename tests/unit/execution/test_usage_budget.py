# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Claude subscription budget guard (operator directive 2026-07-13).

The system must leave ~25% of every 5h subscription window unspent. These tests
pin the estimation model (weighted tokens from CLI transcripts, a trailing-5h
rolling window, a relief horizon that never collapses) and the hardening that
keeps a mistyped knob or odd transcript from silently disabling the guard.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from operations_center.execution.usage_budget import (
    BUCKET_SPAN,
    DEFAULT_CAP_WEIGHTED,
    budget_status,
)
from operations_center.execution.usage_store import UsageStore

NOW = datetime(2026, 7, 13, 19, 0, tzinfo=timezone.utc)


def _write_transcript(projects_dir: Path, name: str, events: list[tuple[datetime, str, dict]]):
    d = projects_dir / name
    d.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"timestamp": ts.isoformat(), "message": {"model": model, "usage": usage}})
        for ts, model, usage in events
    ]
    (d / "session.jsonl").write_text("\n".join(lines) + "\n")


def _env(monkeypatch, tmp_path: Path, cap: float = 1000.0, reserve: float = 0.25):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("OC_CLAUDE_BUDGET_CAP_WEIGHTED", str(cap))
    monkeypatch.setenv("OC_CLAUDE_BUDGET_RESERVE", str(reserve))
    monkeypatch.delenv("OC_CLAUDE_BUDGET_DISABLED", raising=False)
    return tmp_path / "projects"


def test_no_usage_not_exhausted(monkeypatch, tmp_path: Path):
    _env(monkeypatch, tmp_path)
    s = budget_status(now=NOW)
    assert not s.exhausted
    assert s.used_weighted == 0
    assert s.bucket_end == s.bucket_start + BUCKET_SPAN


def test_weighted_sum_and_model_multiplier(monkeypatch, tmp_path: Path):
    projects = _env(monkeypatch, tmp_path, cap=1_000_000.0)
    ts = NOW - timedelta(minutes=30)
    _write_transcript(
        projects,
        "p1",
        [
            # sonnet (1x): 10 in + 5*20 out = 110
            (ts, "claude-sonnet-5", {"input_tokens": 10, "output_tokens": 20}),
            # opus-class (5x): 5*(5*20 out) = 500
            (ts, "claude-opus-4-8", {"output_tokens": 20}),
            # haiku (0.33x): 0.33*(0.1*1000 cache_read) = 33
            (ts, "claude-haiku-4-5-20251001", {"cache_read_input_tokens": 1000}),
        ],
    )
    s = budget_status(now=NOW)
    assert round(s.used_weighted) == 643


def test_exhaustion_at_threshold_and_resume_at_horizon(monkeypatch, tmp_path: Path):
    projects = _env(monkeypatch, tmp_path, cap=1000.0, reserve=0.25)
    first = NOW - timedelta(hours=1)
    # 150 output tokens on sonnet = 750 weighted = exactly cap*(1-reserve)
    _write_transcript(projects, "p1", [(first, "claude-sonnet-5", {"output_tokens": 150})])
    s = budget_status(now=NOW)
    assert s.exhausted
    assert s.bucket_start == first
    # the single event ages out of the trailing window one span after its stamp
    assert s.bucket_end == first + BUCKET_SPAN


def test_trailing_window_excludes_events_older_than_5h(monkeypatch, tmp_path: Path):
    projects = _env(monkeypatch, tmp_path, cap=1000.0, reserve=0.25)
    old = NOW - timedelta(hours=6)  # outside the trailing 5h window
    recent = NOW - timedelta(minutes=10)
    _write_transcript(
        projects,
        "p1",
        [
            (old, "claude-sonnet-5", {"output_tokens": 10_000}),  # must NOT be counted
            (recent, "claude-sonnet-5", {"output_tokens": 10}),
        ],
    )
    s = budget_status(now=NOW)
    assert round(s.used_weighted) == 50  # only the in-window event
    assert s.bucket_start == recent
    assert not s.exhausted


def test_does_not_collapse_under_sustained_load(monkeypatch, tmp_path: Path):
    """Regression for audit F2: an earlier boundary-chaining model let ``used``
    collapse toward zero (and fail open) under continuous >5h load — precisely
    the condition the guard exists for. The trailing window must still count the
    true last 5h and hold into the future."""
    projects = _env(monkeypatch, tmp_path, cap=1000.0, reserve=0.25)  # threshold 750
    # 13 events, 30 min apart, spanning the last 6h; each = 5*20 = 100 weighted.
    events = [
        (NOW - timedelta(hours=6) + timedelta(minutes=30 * i), "claude-sonnet-5", {"output_tokens": 20})
        for i in range(13)
    ]
    _write_transcript(projects, "p1", events)
    s = budget_status(now=NOW)
    # Trailing 5h holds 11 of the 13 events -> 1100 weighted, well over threshold.
    assert s.used_weighted >= 1000
    assert s.exhausted
    assert s.bucket_end > NOW  # a meaningful hold, not ~now


def test_reset_horizon_pivots_on_the_event_that_clears_excess(monkeypatch, tmp_path: Path):
    projects = _env(monkeypatch, tmp_path, cap=1000.0, reserve=0.25)  # threshold 750
    # three equal 500-weighted events; used 1500, excess 750. Shedding the two
    # oldest (1000 > 750) clears it, so relief is one span after the 2nd-oldest.
    t0 = NOW - timedelta(hours=3)
    t1 = NOW - timedelta(hours=2)
    t2 = NOW - timedelta(hours=1)
    _write_transcript(
        projects,
        "p1",
        [(t, "claude-sonnet-5", {"output_tokens": 100}) for t in (t0, t1, t2)],
    )
    s = budget_status(now=NOW)
    assert s.exhausted
    assert s.bucket_end == t1 + BUCKET_SPAN


def test_unknown_model_is_expensive_empty_is_neutral(monkeypatch, tmp_path: Path):
    projects = _env(monkeypatch, tmp_path, cap=1_000_000.0)
    ts = NOW - timedelta(minutes=5)
    _write_transcript(
        projects,
        "p1",
        [
            # unrecognised, non-empty id -> most-expensive 5x: 5*(5*10) = 250
            (ts, "gpt-5-codex", {"output_tokens": 10}),
            # no model info -> neutral 1x: 5*10 = 50
            (ts, "", {"output_tokens": 10}),
        ],
    )
    s = budget_status(now=NOW)
    assert round(s.used_weighted) == 300


def test_region_prefixed_opus_id_still_matches(monkeypatch, tmp_path: Path):
    projects = _env(monkeypatch, tmp_path, cap=1_000_000.0)
    _write_transcript(
        projects,
        "p1",
        [(NOW - timedelta(minutes=5), "us.anthropic.claude-opus-4-8", {"output_tokens": 10})],
    )
    s = budget_status(now=NOW)
    assert round(s.used_weighted) == 250  # opus 5x, not the 50 a prefix-miss would give


def test_bad_env_falls_back_and_reserve_clamps(monkeypatch, tmp_path: Path):
    projects = _env(monkeypatch, tmp_path)
    monkeypatch.setenv("OC_CLAUDE_BUDGET_CAP_WEIGHTED", "not-a-number")  # -> default cap
    monkeypatch.setenv("OC_CLAUDE_BUDGET_RESERVE", "2.0")  # -> clamped to 0.95
    _write_transcript(
        projects, "p1", [(NOW - timedelta(minutes=5), "claude-sonnet-5", {"output_tokens": 100})]
    )
    s = budget_status(now=NOW)
    assert s.cap_weighted == DEFAULT_CAP_WEIGHTED
    assert s.reserve_fraction == 0.95
    assert s.threshold_weighted == pytest.approx(DEFAULT_CAP_WEIGHTED * 0.05)
    assert not s.exhausted  # 500 weighted is far under the (huge) default threshold


def test_naive_timestamp_is_counted_not_crashed(monkeypatch, tmp_path: Path):
    projects = _env(monkeypatch, tmp_path, cap=1000.0)
    d = projects / "p1"
    d.mkdir(parents=True, exist_ok=True)
    naive = (NOW - timedelta(minutes=10)).replace(tzinfo=None).isoformat()  # no offset
    (d / "session.jsonl").write_text(
        json.dumps({"timestamp": naive, "message": {"model": "claude-sonnet-5", "usage": {"output_tokens": 20}}})
        + "\n"
    )
    s = budget_status(now=NOW)  # must not raise on the aware/naive compare
    assert round(s.used_weighted) == 100


def _store(monkeypatch, tmp_path: Path) -> UsageStore:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    return UsageStore()


def test_learned_cap_needs_two_samples_then_median(monkeypatch, tmp_path: Path):
    # audit D4: cap is learned from observed account-wide limit events.
    s = _store(monkeypatch, tmp_path)
    assert s.learned_budget_cap() is None  # no observations
    s.record_budget_cap_sample(weighted=40_000_000, now=NOW)
    assert s.learned_budget_cap() is None  # one is not enough to trust
    s.record_budget_cap_sample(weighted=44_000_000, now=NOW + timedelta(hours=2))
    assert s.learned_budget_cap() == 42_000_000  # median of two
    s.record_budget_cap_sample(weighted=30_000_000, now=NOW + timedelta(hours=4))
    assert s.learned_budget_cap() == 40_000_000  # median of three, robust to the low outlier


def test_cap_sample_recency_guard_keeps_one_per_episode(monkeypatch, tmp_path: Path):
    s = _store(monkeypatch, tmp_path)
    s.record_budget_cap_sample(weighted=40_000_000, now=NOW)
    # the engine re-records the same cooldown every iteration — within min_gap, dropped
    s.record_budget_cap_sample(weighted=99_000_000, now=NOW + timedelta(minutes=20))
    s.record_budget_cap_sample(weighted=44_000_000, now=NOW + timedelta(hours=2))  # new episode
    assert s.learned_budget_cap() == 42_000_000  # median of [40M, 44M]; the 99M re-record dropped


def test_budget_status_uses_learned_cap_when_no_env_override(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))  # no transcripts → used 0
    monkeypatch.delenv("OC_CLAUDE_BUDGET_CAP_WEIGHTED", raising=False)
    monkeypatch.delenv("OC_CLAUDE_BUDGET_RESERVE", raising=False)
    s = _store(monkeypatch, tmp_path)
    s.record_budget_cap_sample(weighted=1000.0, now=NOW - timedelta(hours=3))
    s.record_budget_cap_sample(weighted=1000.0, now=NOW - timedelta(hours=1))
    status = budget_status(now=NOW)
    assert status.cap_weighted == 1000.0  # learned, not the 42M seed


def test_env_cap_override_beats_learned(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("OC_CLAUDE_BUDGET_CAP_WEIGHTED", "500")  # explicit override
    s = _store(monkeypatch, tmp_path)
    s.record_budget_cap_sample(weighted=1000.0, now=NOW - timedelta(hours=3))
    s.record_budget_cap_sample(weighted=1000.0, now=NOW - timedelta(hours=1))
    assert budget_status(now=NOW).cap_weighted == 500.0  # env wins over the learned 1000


def test_disabled_env_reports_not_exhausted(monkeypatch, tmp_path: Path):
    projects = _env(monkeypatch, tmp_path, cap=10.0)
    _write_transcript(
        projects, "p1", [(NOW - timedelta(minutes=5), "claude-sonnet-5", {"output_tokens": 999})]
    )
    monkeypatch.setenv("OC_CLAUDE_BUDGET_DISABLED", "true")  # truthy variant, not just "1"
    s = budget_status(now=NOW)
    assert not s.exhausted
    assert s.disabled
    assert s.used_weighted > s.threshold_weighted  # usage still reported honestly
