# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for entrypoints.custodian_sweep.

Covers the pure logic — _delta, _render_body, _find_open_sweep_task,
_discover_targets — without invoking custodian-audit or hitting Plane.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import operations_center.config as config_module
from operations_center.entrypoints.custodian_sweep import main as sweep_module
from operations_center.entrypoints.custodian_sweep.main import (
    _DEFAULT_TIMEOUT_SECONDS,
    _DEDUP_LABEL_PREFIX,
    _delta,
    _discover_targets,
    _find_open_sweep_task,
    _index_open_sweep_tasks,
    _render_body,
    _RepoSweep,
    _RepoTarget,
    _run_custodian_audits,
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


def test_run_custodian_audits_uses_bounded_parallelism(monkeypatch) -> None:
    targets = [
        _RepoTarget("A", Path("/tmp/a")),
        _RepoTarget("B", Path("/tmp/b")),
        _RepoTarget("C", Path("/tmp/c")),
    ]
    seen: dict[str, object] = {}

    class FakeExecutor:
        def __init__(self, *, max_workers: int) -> None:
            seen["max_workers"] = max_workers

        def __enter__(self) -> "FakeExecutor":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def map(self, fn, iterable):
            items = list(iterable)
            seen["repo_keys"] = [item.repo_key for item in items]
            return [fn(item) for item in items]

    monkeypatch.setattr(sweep_module, "ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(
        sweep_module,
        "_run_custodian_audit",
        lambda target, *, timeout_seconds: _RepoSweep(
            repo_key=target.repo_key, envelope=_envelope(C1=1)
        ),
    )

    sweeps = _run_custodian_audits(targets, jobs=8, timeout_seconds=20)

    assert seen == {"max_workers": 3, "repo_keys": ["A", "B", "C"]}
    assert [sweep.repo_key for sweep in sweeps] == ["A", "B", "C"]


def test_run_custodian_audits_falls_back_to_serial_when_jobs_is_one(monkeypatch) -> None:
    targets = [_RepoTarget("A", Path("/tmp/a")), _RepoTarget("B", Path("/tmp/b"))]
    calls: list[str] = []

    monkeypatch.setattr(
        sweep_module,
        "_run_custodian_audit",
        lambda target, *, timeout_seconds: calls.append(target.repo_key)
        or _RepoSweep(repo_key=target.repo_key),
    )

    sweeps = _run_custodian_audits(targets, jobs=1, timeout_seconds=20)

    assert calls == ["A", "B"]
    assert [sweep.repo_key for sweep in sweeps] == ["A", "B"]


def test_main_uses_safer_default_timeout(monkeypatch, tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("ignored: true\n", encoding="utf-8")
    history_path = tmp_path / "history.json"
    seen: dict[str, int] = {}

    monkeypatch.setattr(config_module, "load_settings", lambda path: SimpleNamespace(repos={}))
    monkeypatch.setattr(sweep_module, "_discover_targets", lambda settings: [])

    def _fake_run(targets, *, jobs: int, timeout_seconds: int):
        seen["timeout_seconds"] = timeout_seconds
        return []

    monkeypatch.setattr(sweep_module, "_run_custodian_audits", _fake_run)
    monkeypatch.setattr(
        sweep_module.sys,
        "argv",
        [
            "operations-center-custodian-sweep",
            "--config",
            str(config_path),
            "--history",
            str(history_path),
        ],
    )

    assert sweep_module.main() == 0
    assert seen == {"timeout_seconds": _DEFAULT_TIMEOUT_SECONDS}
    assert '"repos_swept": 0' in capsys.readouterr().out
