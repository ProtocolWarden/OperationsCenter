# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from operations_center.backends import worker_backend_probe
from operations_center.backends.worker_backend_probe import (
    ProbeResult,
    probe_model,
    refresh_cooldowns,
)
from operations_center.execution.usage_store import UsageStore

_NOW = datetime(2026, 5, 31, 8, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _stub_cli_resolution(monkeypatch):
    """Make the probed CLI always resolvable so probe_model exercises the
    injected fake runner. Without this the tests depend on the `claude`/`codex`
    binary being installed on the host (present on dev machines, absent in CI),
    making them pass locally but fail in CI."""
    monkeypatch.setattr(worker_backend_probe, "_resolve", lambda command: f"/usr/bin/{command}")


class _FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_probe_model_ok_on_clean_exit():
    runner = lambda *a, **k: _FakeProc(0, stdout="ok\n")  # noqa: E731
    res = probe_model("claude_code", "sonnet", runner=runner)
    assert res.ok is True


def test_probe_model_not_ok_on_limit_signal_despite_exit0():
    # Some CLIs print a rate-limit notice and still exit 0 — must not read as runnable.
    runner = lambda *a, **k: _FakeProc(0, stdout="You've hit your weekly limit")  # noqa: E731
    res = probe_model("claude_code", "sonnet", runner=runner)
    assert res.ok is False
    assert "limit signal" in res.detail


def test_probe_model_not_ok_on_nonzero_exit():
    runner = lambda *a, **k: _FakeProc(1, stderr="boom")  # noqa: E731
    res = probe_model("claude_code", "sonnet", runner=runner)
    assert res.ok is False


def test_probe_model_not_ok_on_timeout():
    def runner(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    res = probe_model("claude_code", "sonnet", runner=runner)
    assert res.ok is False
    assert "timed out" in res.detail


def _store(tmp_path: Path) -> UsageStore:
    return UsageStore(path=tmp_path / "usage.json")


def test_refresh_clears_only_runnable_models(tmp_path: Path):
    store = _store(tmp_path)
    for model in ("sonnet", "opus"):
        store.record_worker_backend_cooldown(
            worker_backend="claude_code",
            reset_at=_NOW + timedelta(days=3),
            now=_NOW,
            limit_kind="model_weekly",
            model=model,
        )

    # sonnet recovered, opus still limited.
    def fake_probe(backend, model, *, timeout):
        return ProbeResult(backend, model, model == "sonnet", "x")

    report = refresh_cooldowns(store, now=_NOW, probe=fake_probe, backends=("claude_code",))

    assert report["claude_code"] == {"sonnet": True, "opus": False}
    remaining = {c["model"] for c in store.worker_backend_cooldown_details("claude_code", now=_NOW)}
    assert remaining == {"opus"}


def test_refresh_noop_when_nothing_cooling(tmp_path: Path):
    store = _store(tmp_path)
    calls = []

    def fake_probe(backend, model, *, timeout):
        calls.append((backend, model))
        return ProbeResult(backend, model, True, "x")

    report = refresh_cooldowns(store, now=_NOW, probe=fake_probe)
    assert report == {}
    assert calls == []  # never probes when no cooldown is active


def test_refresh_account_wide_cleared_on_first_success(tmp_path: Path):
    store = _store(tmp_path)
    store.record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=_NOW + timedelta(hours=5),
        now=_NOW,
        limit_kind="session_5h",
        model=None,
    )

    def fake_probe(backend, model, *, timeout):
        return ProbeResult(backend, model, True, "x")

    refresh_cooldowns(store, now=_NOW, probe=fake_probe, backends=("claude_code",))

    assert store.worker_backend_blocked_until("claude_code", now=_NOW) is None
