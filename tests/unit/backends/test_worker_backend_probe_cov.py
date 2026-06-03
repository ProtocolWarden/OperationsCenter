# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from operations_center.backends import worker_backend_probe as wbp
from operations_center.backends.limit_classifier import (
    GLOBAL_WEEKLY,
    MODEL_WEEKLY,
    SESSION_5H,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _completed(returncode: int = 0, stdout: str = "ok", stderr: str = "") -> object:
    return subprocess.CompletedProcess(
        args=["x"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class _FakeUsageStore:
    """Minimal usage-store double recording cleared cooldowns."""

    def __init__(self, snapshot: dict, *, removed: int = 2, raise_snapshot: bool = False):
        self._snapshot = snapshot
        self._removed = removed
        self._raise_snapshot = raise_snapshot
        self.snapshot_calls: list = []
        self.cleared: list[dict] = []

    def current_worker_backend_cooldowns(self, *, now):
        self.snapshot_calls.append(now)
        if self._raise_snapshot:
            raise RuntimeError("boom")
        return self._snapshot

    def clear_worker_backend_cooldown(self, *, worker_backend, model, now, include_account_wide):
        self.cleared.append(
            {
                "worker_backend": worker_backend,
                "model": model,
                "now": now,
                "include_account_wide": include_account_wide,
            }
        )
        return self._removed


# --------------------------------------------------------------------------- #
# ProbeResult dataclass
# --------------------------------------------------------------------------- #
def test_probe_result_is_frozen_dataclass():
    r = wbp.ProbeResult("claude_code", "sonnet", True, "runnable")
    assert r.worker_backend == "claude_code"
    assert r.model == "sonnet"
    assert r.ok is True
    assert r.detail == "runnable"
    with pytest.raises(Exception):
        r.ok = False  # frozen


# --------------------------------------------------------------------------- #
# _resolve
# --------------------------------------------------------------------------- #
def test_resolve_uses_shutil_which_when_found(monkeypatch):
    monkeypatch.setattr(wbp.shutil, "which", lambda cmd: "/usr/bin/claude")
    assert wbp._resolve("claude") == "/usr/bin/claude"


def test_resolve_falls_back_to_local_bin(monkeypatch, tmp_path):
    monkeypatch.setattr(wbp.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(wbp.Path, "home", staticmethod(lambda: tmp_path))
    target = tmp_path / ".local" / "bin" / "claude"
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\n")
    assert wbp._resolve("claude") == str(target)


def test_resolve_falls_back_to_home_bin(monkeypatch, tmp_path):
    monkeypatch.setattr(wbp.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(wbp.Path, "home", staticmethod(lambda: tmp_path))
    target = tmp_path / "bin" / "claude"
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\n")
    assert wbp._resolve("claude") == str(target)


def test_resolve_codex_nvm_glob(monkeypatch, tmp_path):
    monkeypatch.setattr(wbp.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(wbp.Path, "home", staticmethod(lambda: tmp_path))
    nvm = tmp_path / ".nvm" / "versions" / "node" / "v20.0.0" / "bin"
    nvm.mkdir(parents=True)
    codex = nvm / "codex"
    codex.write_text("#!/bin/sh\n")
    assert wbp._resolve("codex") == str(codex)


def test_resolve_returns_none_when_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(wbp.shutil, "which", lambda cmd: None)
    monkeypatch.setattr(wbp.Path, "home", staticmethod(lambda: tmp_path))
    assert wbp._resolve("claude") is None


# --------------------------------------------------------------------------- #
# _probe_command
# --------------------------------------------------------------------------- #
def test_probe_command_claude_happy(monkeypatch):
    monkeypatch.setattr(wbp, "_resolve", lambda cmd: "/bin/claude")
    cmd = wbp._probe_command("claude_code", "sonnet")
    assert cmd == [
        "/bin/claude",
        "-p",
        wbp.PROBE_PROMPT,
        "--model",
        "sonnet",
        "--dangerously-skip-permissions",
        "--output-format",
        "text",
    ]


def test_probe_command_claude_unknown_model_returns_none(monkeypatch):
    monkeypatch.setattr(wbp, "_resolve", lambda cmd: "/bin/claude")
    assert wbp._probe_command("claude_code", "gpt-4") is None


def test_probe_command_claude_missing_binary_returns_none(monkeypatch):
    monkeypatch.setattr(wbp, "_resolve", lambda cmd: None)
    assert wbp._probe_command("claude_code", "opus") is None


def test_probe_command_codex_happy(monkeypatch):
    monkeypatch.setattr(wbp, "_resolve", lambda cmd: "/bin/codex")
    cmd = wbp._probe_command("codex_cli", "codex")
    assert cmd == [
        "/bin/codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        wbp.PROBE_PROMPT,
    ]


def test_probe_command_codex_missing_binary_returns_none(monkeypatch):
    monkeypatch.setattr(wbp, "_resolve", lambda cmd: None)
    assert wbp._probe_command("codex_cli", "codex") is None


def test_probe_command_unknown_backend_returns_none():
    assert wbp._probe_command("mystery_backend", "x") is None


# --------------------------------------------------------------------------- #
# probe_model
# --------------------------------------------------------------------------- #
def test_probe_model_unsupported_returns_not_ok(monkeypatch):
    monkeypatch.setattr(wbp, "_probe_command", lambda wb, m: None)
    res = wbp.probe_model("claude_code", "sonnet")
    assert res.ok is False
    assert res.detail == "cli unavailable or unsupported model"
    assert res.worker_backend == "claude_code"
    assert res.model == "sonnet"


def test_probe_model_success(monkeypatch):
    monkeypatch.setattr(wbp, "_probe_command", lambda wb, m: ["/bin/claude"])
    runner = lambda *a, **k: _completed(returncode=0, stdout="ok")  # noqa: E731
    res = wbp.probe_model("claude_code", "opus", runner=runner)
    assert res.ok is True
    assert res.detail == "runnable"


def test_probe_model_nonzero_exit(monkeypatch):
    monkeypatch.setattr(wbp, "_probe_command", lambda wb, m: ["/bin/claude"])
    runner = lambda *a, **k: _completed(returncode=3, stdout="", stderr="nope")  # noqa: E731
    res = wbp.probe_model("claude_code", "opus", runner=runner)
    assert res.ok is False
    assert res.detail == "exit 3"


def test_probe_model_limit_signal_despite_zero_exit(monkeypatch):
    monkeypatch.setattr(wbp, "_probe_command", lambda wb, m: ["/bin/claude"])
    # session window message → SESSION_5H, exit 0.
    runner = lambda *a, **k: _completed(  # noqa: E731
        returncode=0, stdout="hit your 5-hour session limit"
    )
    res = wbp.probe_model("claude_code", "sonnet", runner=runner)
    assert res.ok is False
    assert res.detail == f"limit signal ({SESSION_5H})"


def test_probe_model_timeout(monkeypatch):
    monkeypatch.setattr(wbp, "_probe_command", lambda wb, m: ["/bin/claude"])

    def _runner(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=k["timeout"])

    res = wbp.probe_model("claude_code", "opus", timeout=12, runner=_runner)
    assert res.ok is False
    assert res.detail == "probe timed out after 12s"


def test_probe_model_os_error(monkeypatch):
    monkeypatch.setattr(wbp, "_probe_command", lambda wb, m: ["/bin/claude"])

    def _runner(*a, **k):
        raise OSError("exec format error")

    res = wbp.probe_model("claude_code", "opus", runner=_runner)
    assert res.ok is False
    assert res.detail.startswith("probe error:")
    assert "exec format error" in res.detail


def test_probe_model_subprocess_error(monkeypatch):
    monkeypatch.setattr(wbp, "_probe_command", lambda wb, m: ["/bin/claude"])

    def _runner(*a, **k):
        raise subprocess.SubprocessError("weird")

    res = wbp.probe_model("claude_code", "opus", runner=_runner)
    assert res.ok is False
    assert "weird" in res.detail


def test_probe_model_handles_none_stdout_stderr(monkeypatch):
    monkeypatch.setattr(wbp, "_probe_command", lambda wb, m: ["/bin/claude"])
    runner = lambda *a, **k: _completed(returncode=0, stdout=None, stderr=None)  # noqa: E731
    res = wbp.probe_model("claude_code", "haiku", runner=runner)
    assert res.ok is True


def test_probe_model_passes_timeout_to_runner(monkeypatch):
    monkeypatch.setattr(wbp, "_probe_command", lambda wb, m: ["/bin/claude"])
    seen = {}

    def _runner(cmd, **k):
        seen.update(k)
        return _completed()

    wbp.probe_model("claude_code", "opus", timeout=55, runner=_runner)
    assert seen["timeout"] == 55
    assert seen["capture_output"] is True
    assert seen["text"] is True


# --------------------------------------------------------------------------- #
# _models_to_probe
# --------------------------------------------------------------------------- #
def test_models_to_probe_per_model_only():
    details = [
        {"limit_kind": MODEL_WEEKLY, "model": "sonnet"},
        {"limit_kind": MODEL_WEEKLY, "model": "opus"},
    ]
    models, account_wide = wbp._models_to_probe(details, "claude_code")
    assert models == {"sonnet", "opus"}
    assert account_wide is False


def test_models_to_probe_account_wide_probes_all():
    details = [{"limit_kind": SESSION_5H, "model": None}]
    models, account_wide = wbp._models_to_probe(details, "claude_code")
    assert account_wide is True
    assert models == {"sonnet", "opus", "haiku"}


def test_models_to_probe_model_weekly_without_model_is_account_wide():
    # limit_kind matches MODEL_WEEKLY but model falsey → else branch.
    details = [{"limit_kind": MODEL_WEEKLY, "model": ""}]
    models, account_wide = wbp._models_to_probe(details, "codex_cli")
    assert account_wide is True
    assert models == {"codex"}


def test_models_to_probe_global_weekly_account_wide():
    details = [{"limit_kind": GLOBAL_WEEKLY}]
    models, account_wide = wbp._models_to_probe(details, "claude_code")
    assert account_wide is True
    assert models == {"sonnet", "opus", "haiku"}


def test_models_to_probe_unknown_backend_account_wide_empty():
    details = [{"limit_kind": GLOBAL_WEEKLY}]
    models, account_wide = wbp._models_to_probe(details, "nope")
    assert account_wide is True
    assert models == set()


def test_models_to_probe_empty_details():
    models, account_wide = wbp._models_to_probe([], "claude_code")
    assert models == set()
    assert account_wide is False


# --------------------------------------------------------------------------- #
# refresh_cooldowns
# --------------------------------------------------------------------------- #
def test_refresh_cooldowns_snapshot_raises_returns_empty():
    store = _FakeUsageStore({}, raise_snapshot=True)
    report = wbp.refresh_cooldowns(store)
    assert report == {}
    assert store.cleared == []


def test_refresh_cooldowns_uses_default_now(monkeypatch):
    fixed = datetime(2026, 6, 2, tzinfo=UTC)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(wbp, "datetime", _DT)
    store = _FakeUsageStore({})
    wbp.refresh_cooldowns(store)
    assert store.snapshot_calls == [fixed]


def test_refresh_cooldowns_no_cooling_skips_backend():
    snapshot = {"claude_code": {"cooling_down": False, "cooldowns": []}}
    store = _FakeUsageStore(snapshot)
    report = wbp.refresh_cooldowns(store, backends=("claude_code",))
    assert report == {}
    assert store.cleared == []


def test_refresh_cooldowns_cooling_but_no_models_skips():
    # cooling_down True but details empty -> _models_to_probe returns empty set.
    snapshot = {"claude_code": {"cooling_down": True, "cooldowns": []}}
    store = _FakeUsageStore(snapshot)
    report = wbp.refresh_cooldowns(store, backends=("claude_code",))
    assert report == {}
    assert store.cleared == []


def test_refresh_cooldowns_per_model_success_clears(monkeypatch):
    snapshot = {
        "claude_code": {
            "cooling_down": True,
            "cooldowns": [{"limit_kind": MODEL_WEEKLY, "model": "sonnet"}],
        }
    }
    store = _FakeUsageStore(snapshot, removed=1)
    monkeypatch.setattr(
        wbp,
        "probe_model",
        lambda wb, m, *, timeout: wbp.ProbeResult(wb, m, True, "runnable"),
    )
    now = datetime(2026, 6, 2, tzinfo=UTC)
    report = wbp.refresh_cooldowns(store, now=now, backends=("claude_code",))
    assert report == {"claude_code": {"sonnet": True}}
    assert len(store.cleared) == 1
    assert store.cleared[0]["model"] == "sonnet"
    # per-model only -> account_wide passed as False
    assert store.cleared[0]["include_account_wide"] is False
    assert store.cleared[0]["now"] == now


def test_refresh_cooldowns_failed_probe_does_not_clear(monkeypatch):
    snapshot = {
        "claude_code": {
            "cooling_down": True,
            "cooldowns": [{"limit_kind": MODEL_WEEKLY, "model": "opus"}],
        }
    }
    store = _FakeUsageStore(snapshot)
    monkeypatch.setattr(
        wbp,
        "probe_model",
        lambda wb, m, *, timeout: wbp.ProbeResult(wb, m, False, "exit 1"),
    )
    report = wbp.refresh_cooldowns(store, backends=("claude_code",))
    assert report == {"claude_code": {"opus": False}}
    assert store.cleared == []


def test_refresh_cooldowns_account_wide_cleared_only_on_first_success(monkeypatch):
    snapshot = {
        "claude_code": {
            "cooling_down": True,
            "cooldowns": [{"limit_kind": SESSION_5H, "model": None}],
        }
    }
    store = _FakeUsageStore(snapshot)
    # All probes succeed; sorted models: haiku, opus, sonnet.
    monkeypatch.setattr(
        wbp,
        "probe_model",
        lambda wb, m, *, timeout: wbp.ProbeResult(wb, m, True, "runnable"),
    )
    report = wbp.refresh_cooldowns(store, backends=("claude_code",))
    assert report == {"claude_code": {"haiku": True, "opus": True, "sonnet": True}}
    # first cleared (haiku) carries include_account_wide=True, rest False.
    assert [c["model"] for c in store.cleared] == ["haiku", "opus", "sonnet"]
    assert store.cleared[0]["include_account_wide"] is True
    assert store.cleared[1]["include_account_wide"] is False
    assert store.cleared[2]["include_account_wide"] is False


def test_refresh_cooldowns_account_wide_first_fails_keeps_flag(monkeypatch):
    snapshot = {
        "claude_code": {
            "cooling_down": True,
            "cooldowns": [{"limit_kind": SESSION_5H, "model": None}],
        }
    }
    store = _FakeUsageStore(snapshot)

    # haiku fails, opus succeeds -> opus should carry account_wide True.
    def _probe(wb, m, *, timeout):
        return wbp.ProbeResult(wb, m, m != "haiku", "ok" if m != "haiku" else "down")

    monkeypatch.setattr(wbp, "probe_model", _probe)
    wbp.refresh_cooldowns(store, backends=("claude_code",))
    cleared = {c["model"]: c["include_account_wide"] for c in store.cleared}
    assert cleared == {"opus": True, "sonnet": False}
    assert "haiku" not in cleared


def test_refresh_cooldowns_uses_injected_probe(monkeypatch):
    snapshot = {
        "codex_cli": {
            "cooling_down": True,
            "cooldowns": [{"limit_kind": MODEL_WEEKLY, "model": "codex"}],
        }
    }
    store = _FakeUsageStore(snapshot)
    calls = []

    def _probe(wb, m, *, timeout):
        calls.append((wb, m, timeout))
        return wbp.ProbeResult(wb, m, True, "ok")

    report = wbp.refresh_cooldowns(store, backends=("codex_cli",), probe=_probe, timeout=7)
    assert report == {"codex_cli": {"codex": True}}
    assert calls == [("codex_cli", "codex", 7)]


def test_refresh_cooldowns_logger_called_on_success_and_failure(monkeypatch):
    snapshot = {
        "claude_code": {
            "cooling_down": True,
            "cooldowns": [
                {"limit_kind": MODEL_WEEKLY, "model": "sonnet"},
                {"limit_kind": MODEL_WEEKLY, "model": "opus"},
            ],
        }
    }
    store = _FakeUsageStore(snapshot, removed=3)
    logs: list[str] = []

    def _probe(wb, m, *, timeout):
        return wbp.ProbeResult(wb, m, m == "sonnet", "runnable" if m == "sonnet" else "exit 1")

    wbp.refresh_cooldowns(store, backends=("claude_code",), probe=_probe, logger=logs.append)
    joined = "\n".join(logs)
    assert "claude_code/sonnet runnable — cleared 3 stale" in joined
    assert "claude_code/opus still limited (exit 1)" in joined


def test_refresh_cooldowns_no_logger_does_not_error(monkeypatch):
    snapshot = {
        "claude_code": {
            "cooling_down": True,
            "cooldowns": [{"limit_kind": MODEL_WEEKLY, "model": "sonnet"}],
        }
    }
    store = _FakeUsageStore(snapshot)

    def _probe(wb, m, *, timeout):
        return wbp.ProbeResult(wb, m, True, "ok")

    report = wbp.refresh_cooldowns(store, backends=("claude_code",), probe=_probe)
    assert report["claude_code"]["sonnet"] is True


def test_refresh_cooldowns_missing_backend_in_snapshot_skipped():
    # backend requested but absent from snapshot -> status {} -> skipped.
    store = _FakeUsageStore({})
    report = wbp.refresh_cooldowns(store, backends=("claude_code", "codex_cli"))
    assert report == {}


def test_refresh_cooldowns_cooldowns_none_uses_empty_list():
    # 'cooldowns' is None and cooling_down False -> skip without error.
    snapshot = {"claude_code": {"cooling_down": False, "cooldowns": None}}
    store = _FakeUsageStore(snapshot)
    report = wbp.refresh_cooldowns(store, backends=("claude_code",))
    assert report == {}


def test_refresh_cooldowns_cooling_down_true_cooldowns_none(monkeypatch):
    # cooling_down True but cooldowns None -> details=[] -> no models -> skip.
    snapshot = {"claude_code": {"cooling_down": True, "cooldowns": None}}
    store = _FakeUsageStore(snapshot)
    report = wbp.refresh_cooldowns(store, backends=("claude_code",))
    assert report == {}
    assert store.cleared == []


def test_module_constants():
    assert wbp.DEFAULT_PROBE_TIMEOUT_SECONDS == 90
    assert "ok" in wbp.PROBE_PROMPT
    assert wbp._CLAUDE_MODEL_ALIASES == {"sonnet", "opus", "haiku"}
    assert isinstance(Path.home(), Path)
