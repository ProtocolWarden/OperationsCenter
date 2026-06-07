# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for the pytest flaky-detection plugin.

The plugin class is exercised directly (not via a pytester run) so its
hooks are covered without loading the pytest11 entry point — which would
import the observer package before coverage instrumentation starts.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from operations_center.observer.pytest_flaky_plugin import (
    FlakyTestDetectionPlugin,
    pytest_addoption,
    pytest_configure,
)


def _call_info(when: str = "call", excinfo=None, duration: float = 0.5):
    return SimpleNamespace(when=when, excinfo=excinfo, duration=duration)


def _item(nodeid: str):
    return SimpleNamespace(nodeid=nodeid)


def test_plugin_init_creates_storage_dir(tmp_path: Path) -> None:
    storage = tmp_path / "flaky"
    plugin = FlakyTestDetectionPlugin(str(storage))
    assert storage.is_dir()
    assert plugin.test_outcomes == {}
    assert plugin.session_start_time is None


def test_sessionstart_resets_state(tmp_path: Path) -> None:
    plugin = FlakyTestDetectionPlugin(str(tmp_path / "flaky"))
    plugin.test_outcomes = {"stale": {}}
    plugin.pytest_sessionstart(session=SimpleNamespace(name="s"))
    assert plugin.test_outcomes == {}
    assert plugin.session_start_time is not None


def test_makereport_captures_pass_and_fail(tmp_path: Path) -> None:
    plugin = FlakyTestDetectionPlugin(str(tmp_path / "flaky"))

    plugin.pytest_runtest_makereport(_item("tests/a.py::test_ok"), _call_info())
    exc = SimpleNamespace(value=AssertionError("boom"))
    plugin.pytest_runtest_makereport(_item("tests/a.py::test_bad"), _call_info(excinfo=exc))

    ok = plugin.test_outcomes["tests/a.py::test_ok"]
    bad = plugin.test_outcomes["tests/a.py::test_bad"]
    assert ok["outcome"] == "passed" and ok["exception"] is None
    assert bad["outcome"] == "failed" and "boom" in bad["exception"]


def test_makereport_ignores_setup_and_teardown(tmp_path: Path) -> None:
    plugin = FlakyTestDetectionPlugin(str(tmp_path / "flaky"))
    plugin.pytest_runtest_makereport(_item("tests/a.py::test_x"), _call_info(when="setup"))
    plugin.pytest_runtest_makereport(_item("tests/a.py::test_x"), _call_info(when="teardown"))
    assert plugin.test_outcomes == {}


def test_makereport_updates_existing_entry(tmp_path: Path) -> None:
    plugin = FlakyTestDetectionPlugin(str(tmp_path / "flaky"))
    nodeid = "tests/a.py::test_retry"
    exc = SimpleNamespace(value=RuntimeError("first failure"))
    plugin.pytest_runtest_makereport(_item(nodeid), _call_info(excinfo=exc))
    plugin.pytest_runtest_makereport(_item(nodeid), _call_info(duration=1.25))

    entry = plugin.test_outcomes[nodeid]
    assert entry["outcome"] == "passed"
    assert entry["duration"] == 1.25
    assert entry["exception"] is None


def test_sessionfinish_noop_without_outcomes(tmp_path: Path) -> None:
    storage = tmp_path / "flaky"
    plugin = FlakyTestDetectionPlugin(str(storage))
    plugin.pytest_sessionfinish(session=SimpleNamespace(name="s"), exitstatus=0)
    assert list(storage.glob("runs/**/*.json")) == []


def test_sessionfinish_writes_report_with_flaky_candidates(tmp_path: Path) -> None:
    storage = tmp_path / "flaky"
    plugin = FlakyTestDetectionPlugin(str(storage))
    plugin.pytest_sessionstart(session=SimpleNamespace(name="s"))

    plugin.pytest_runtest_makereport(_item("tests/a.py::test_ok"), _call_info())
    exc = SimpleNamespace(value=ValueError("flake"))
    plugin.pytest_runtest_makereport(_item("tests/a.py::test_bad"), _call_info(excinfo=exc))

    plugin.pytest_sessionfinish(session=SimpleNamespace(name="sess-1"), exitstatus=1)

    reports = list(storage.glob("runs/*/*-session.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["session_count"] == 2
    assert report["passed_count"] == 1
    assert report["failed_count"] == 1
    assert len(report["flaky_candidates"]) == 1
    assert report["flaky_candidates"][0]["test_name"] == "tests/a.py::test_bad"
    assert report["flaky_candidates"][0]["module"] == "tests/a.py"


def test_save_session_report_warning_on_io_error(tmp_path: Path, monkeypatch, caplog) -> None:
    plugin = FlakyTestDetectionPlugin(str(tmp_path / "flaky"))

    def _raise(*args, **kwargs):
        raise IOError("disk full")

    monkeypatch.setattr("builtins.open", _raise)
    with caplog.at_level("WARNING"):
        plugin._save_session_report({"session_id": "s"})
    assert any("Failed to save flaky test metrics" in r.message for r in caplog.records)


def test_addoption_registers_flags() -> None:
    registered: list[str] = []
    parser = SimpleNamespace(
        addoption=lambda name, **kwargs: registered.append(name),
    )
    pytest_addoption(parser)
    assert registered == ["--flaky-detection", "--flaky-storage"]


def test_configure_registers_plugin_when_enabled(tmp_path: Path) -> None:
    registered: dict[str, object] = {}

    options = {"--flaky-detection": True, "--flaky-storage": str(tmp_path / "flaky")}
    config = SimpleNamespace(
        getoption=lambda name: options[name],
        pluginmanager=SimpleNamespace(
            register=lambda plugin, name: registered.update({name: plugin})
        ),
    )
    pytest_configure(config)
    assert isinstance(registered.get("flaky_detection"), FlakyTestDetectionPlugin)


def test_configure_skips_when_disabled(tmp_path: Path) -> None:
    registered: dict[str, object] = {}
    options = {"--flaky-detection": False, "--flaky-storage": str(tmp_path / "flaky")}
    config = SimpleNamespace(
        getoption=lambda name: options[name],
        pluginmanager=SimpleNamespace(
            register=lambda plugin, name: registered.update({name: plugin})
        ),
    )
    pytest_configure(config)
    assert registered == {}
