# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for entrypoints.custodian_sweep.

Covers the pure logic — _delta, _render_body, _find_open_sweep_task,
_discover_targets — without invoking custodian-audit or hitting Plane.
"""

from __future__ import annotations

import threading
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from operations_center.entrypoints.custodian_sweep.main import (
    _DEFAULT_AUDIT_TIMEOUT_SECONDS,
    _DEDUP_LABEL_PREFIX,
    _delta,
    _discover_targets,
    _find_open_sweep_task,
    _index_open_sweep_tasks,
    _render_body,
    _run_custodian_audit,
    _run_custodian_audits,
    _RepoSweep,
    _RepoTarget,
    _sweep_max_workers,
)


def _envelope(**counts: int) -> dict:
    return {
        "schema_version": 1,
        "repo_key": "Demo",
        "total_findings": sum(counts.values()),
        "patterns": {
            det_id: {"description": f"{det_id} desc", "status": "open", "count": n}
            for det_id, n in counts.items()
        },
    }


def test_delta_computes_per_detector_change() -> None:
    current = _envelope(C3=14, OC7=8, DC2=5)
    previous = _envelope(C3=12, OC7=8)  # DC2 absent -> baseline 0
    assert _delta(current, previous) == {"C3": 2, "OC7": 0, "DC2": 5}


def test_delta_handles_no_previous_snapshot() -> None:
    current = _envelope(C3=3)
    assert _delta(current, None) == {"C3": 3}


def test_render_body_includes_table_and_drilldown() -> None:
    sweep = _RepoSweep(repo_key="Demo", envelope=_envelope(C3=2, DC1=1))
    body = _render_body(sweep, {"C3": 2, "DC1": 0})
    assert "| `C3`" in body
    assert "| `DC1`" in body
    assert "+2" in body
    assert "—" in body  # zero delta renders as em-dash
    assert "custodian-audit --repo <path-to-Demo>" in body


def test_render_body_for_error_sweep() -> None:
    sweep = _RepoSweep(repo_key="Demo", error="custodian-audit not on PATH")
    body = _render_body(sweep, {})
    assert "Custodian sweep error for Demo" in body
    assert "custodian-audit not on PATH" in body


def test_find_open_sweep_task_matches_dedup_label() -> None:
    plane = SimpleNamespace(
        list_issues=lambda: [
            {
                "id": "1",
                "state": {"name": "Done"},
                "labels": [{"name": f"{_DEDUP_LABEL_PREFIX}Demo"}],
            },  # closed → skip
            {
                "id": "2",
                "state": {"name": "Backlog"},
                "labels": [{"name": "unrelated"}],
            },  # wrong label
            {
                "id": "3",
                "state": {"name": "Backlog"},
                "labels": [{"name": f"{_DEDUP_LABEL_PREFIX}Demo"}],
            },  # match
        ]
    )
    found = _find_open_sweep_task(plane, "Demo")
    assert found is not None and found["id"] == "3"


def test_find_open_sweep_task_returns_none_when_absent() -> None:
    plane = SimpleNamespace(list_issues=lambda: [])
    assert _find_open_sweep_task(plane, "Demo") is None


def test_index_open_sweep_tasks_maps_repo_key_to_issue() -> None:
    plane = SimpleNamespace(
        list_issues=lambda: [
            {
                "id": "3",
                "state": {"name": "Backlog"},
                "labels": [{"name": f"{_DEDUP_LABEL_PREFIX}Demo"}],
            },
            {
                "id": "4",
                "state": {"name": "Done"},
                "labels": [{"name": f"{_DEDUP_LABEL_PREFIX}Skip"}],
            },
        ]
    )
    indexed = _index_open_sweep_tasks(plane)
    assert indexed == {
        "Demo": {
            "id": "3",
            "state": {"name": "Backlog"},
            "labels": [{"name": f"{_DEDUP_LABEL_PREFIX}Demo"}],
        }
    }


def test_discover_targets_filters_to_repos_with_custodian_yaml(tmp_path: Path) -> None:
    has_yaml = tmp_path / "WithYaml"
    has_yaml.mkdir()
    (has_yaml / ".custodian.yaml").write_text("repo_key: WithYaml\n")
    no_yaml = tmp_path / "NoYaml"
    no_yaml.mkdir()
    settings = SimpleNamespace(
        repos={
            "WithYaml": SimpleNamespace(local_path=str(has_yaml)),
            "NoYaml": SimpleNamespace(local_path=str(no_yaml)),
            "NoCheckout": SimpleNamespace(local_path=None),
        }
    )
    targets = _discover_targets(settings)
    assert [t.repo_key for t in targets] == ["WithYaml"]
    assert isinstance(targets[0], _RepoTarget)


def test_sweep_max_workers_is_bounded() -> None:
    assert _sweep_max_workers(0) == 1
    assert _sweep_max_workers(1) == 1
    assert 2 <= _sweep_max_workers(4) <= 4
    assert _sweep_max_workers(99) <= 8


def test_run_custodian_audits_overlaps_slow_repos(monkeypatch) -> None:
    targets = [
        _RepoTarget(repo_key="A", local_path=Path("/tmp/A")),
        _RepoTarget(repo_key="B", local_path=Path("/tmp/B")),
    ]
    started: deque[str] = deque()
    lock = threading.Lock()
    release = threading.Event()
    both_started = threading.Event()

    def fake_run(target: _RepoTarget, *, timeout_seconds: int) -> _RepoSweep:
        with lock:
            started.append(target.repo_key)
            if len(started) == 2:
                both_started.set()
        both_started.wait(timeout=1)
        if target.repo_key == "A":
            release.wait(timeout=1)
        else:
            release.set()
        return _RepoSweep(repo_key=target.repo_key, envelope=_envelope(C1=1))

    monkeypatch.setattr(
        "operations_center.entrypoints.custodian_sweep.main._run_custodian_audit",
        fake_run,
    )

    sweeps = _run_custodian_audits(targets)

    assert list(started) == ["A", "B"]
    assert [s.repo_key for s in sweeps] == ["A", "B"]


def test_run_custodian_audit_uses_bounded_default_timeout(monkeypatch) -> None:
    target = _RepoTarget(repo_key="Demo", local_path=Path("/tmp/Demo"))

    monkeypatch.setattr(
        "operations_center.entrypoints.custodian_sweep.main._resolve_custodian_audit",
        lambda: "/tmp/custodian-audit",
    )

    with patch("operations_center.entrypoints.custodian_sweep.main.subprocess.run") as run:
        run.return_value = SimpleNamespace(returncode=0, stdout='{"total_findings": 0}', stderr="")
        sweep = _run_custodian_audit(target)

    assert sweep.error is None
    assert run.call_args.kwargs["timeout"] == _DEFAULT_AUDIT_TIMEOUT_SECONDS
