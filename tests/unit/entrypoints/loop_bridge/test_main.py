# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""loop_bridge — the OC-specific hooks behind the PseudoOperator loop.

Ports the OC-unique tests from the old tools/loop controller test file
(watcher bounce semantics, usage-store seed/bridge); the generic loop logic
(parsers, ladder, caps) is covered in ContextLifecycle's own tests.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from operations_center.entrypoints.loop_bridge import main as bridge
from operations_center.execution.usage_store import UsageStore


# ── seed-cooldowns ────────────────────────────────────────────────────────────


def test_seed_cooldowns_maps_models_to_backends(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    now = datetime.now(timezone.utc)
    reset_at = now + timedelta(days=2)
    store = UsageStore()
    for model in ("sonnet", "opus"):
        store.record_worker_backend_cooldown(
            worker_backend="claude_code",
            reset_at=reset_at,
            now=now,
            limit_kind="model_weekly",
            model=model,
        )
    assert bridge.seed_cooldowns() == 0
    out = json.loads(capsys.readouterr().out)
    expected = reset_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert out["claude"] == expected
    assert out["opus"] == expected
    assert out["codex"] is None


def test_seed_cooldowns_account_wide_limit_cools_both(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    now = datetime.now(timezone.utc)
    reset_at = now + timedelta(hours=3)
    UsageStore().record_worker_backend_cooldown(
        worker_backend="claude_code",
        reset_at=reset_at,
        now=now,
        limit_kind="session_5h",
        model=None,
    )
    assert bridge.seed_cooldowns() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["claude"] is not None and out["opus"] is not None


# ── on-cooldown ───────────────────────────────────────────────────────────────


def test_on_cooldown_records_into_usage_store(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    reset_at = datetime.now(timezone.utc) + timedelta(hours=2)
    payload = json.dumps(
        {
            "backend": "claude",
            "reset_at": reset_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit_kind": "model_weekly",
            "model": "claude-sonnet-5",
        }
    )
    assert bridge.on_cooldown(payload) == 0
    snapshot = UsageStore().current_worker_backend_cooldowns(now=datetime.now(timezone.utc))
    details = snapshot.get("claude_code", {}).get("cooldowns", [])
    assert any(
        d.get("model") == "sonnet" and d.get("limit_kind") == "model_weekly" for d in details
    )


def test_on_cooldown_session5h_records_budget_cap_sample(monkeypatch, tmp_path: Path) -> None:
    # audit D4: an account-wide claude limit calibrates the budget cap.
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    cfg = tmp_path / "cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("OC_CLAUDE_BUDGET_CAP_WEIGHTED", raising=False)
    proj = cfg / "projects" / "p1"
    proj.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    proj.joinpath("s.jsonl").write_text(
        json.dumps(
            {"timestamp": now.isoformat(), "message": {"model": "claude-sonnet-5", "usage": {"output_tokens": 100}}}
        )
        + "\n"
    )
    reset_at = now + timedelta(hours=2)
    payload = json.dumps(
        {
            "backend": "claude",
            "reset_at": reset_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit_kind": "session_5h",
            "model": None,
        }
    )
    assert bridge.on_cooldown(payload) == 0
    # the estimator's 500 weighted (5*100 output) is captured as a cap observation
    assert UsageStore().learned_budget_cap(min_samples=1) == 500.0


def test_on_cooldown_model_weekly_records_no_cap_sample(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    reset_at = datetime.now(timezone.utc) + timedelta(hours=2)
    payload = json.dumps(
        {
            "backend": "claude",
            "reset_at": reset_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit_kind": "model_weekly",  # per-model, NOT account-wide → no calibration
            "model": "claude-sonnet-5",
        }
    )
    assert bridge.on_cooldown(payload) == 0
    assert UsageStore().learned_budget_cap(min_samples=1) is None


def test_on_cooldown_unknown_backend_is_noop(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    payload = json.dumps(
        {"backend": "weird", "reset_at": "2027-01-01T00:00:00Z", "limit_kind": "x", "model": None}
    )
    assert bridge.on_cooldown(payload) == 0


# ── self-update / watcher bounce ──────────────────────────────────────────────


def _seed_watcher_pidfiles(monkeypatch, tmp_path: Path, roles_to_pids: dict[str, int]) -> Path:
    watch_dir = tmp_path / "watch-all"
    watch_dir.mkdir()
    for role, pid in roles_to_pids.items():
        (watch_dir / f"{role}.pid").write_text(str(pid), encoding="utf-8")
    monkeypatch.setattr(bridge, "WATCH_DIR", watch_dir)
    return watch_dir


def test_restart_watchers_bounces_child_not_wrapper(monkeypatch, tmp_path: Path) -> None:
    # The pid file holds the wrapper pid; the bounce must SIGTERM the wrapper's
    # *children* (pkill -P), never os.kill the wrapper itself.
    _seed_watcher_pidfiles(monkeypatch, tmp_path, {"review": 4242})
    monkeypatch.setattr(bridge.os, "kill", lambda pid, sig: None)  # wrapper alive
    calls: list[list[str]] = []
    monkeypatch.setattr(bridge.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or None)

    bridge._restart_watchers()

    assert calls == [["pkill", "-TERM", "-P", "4242", "-f", bridge._WATCHER_CHILD_MATCH]]


def test_restart_watchers_never_touches_watchdog(monkeypatch, tmp_path: Path) -> None:
    # The watchdog is the only reviver — it must outlive every restart.
    _seed_watcher_pidfiles(monkeypatch, tmp_path, {"watchdog": 999, "review": 4242})
    monkeypatch.setattr(bridge.os, "kill", lambda pid, sig: None)
    bounced: list[str] = []
    monkeypatch.setattr(bridge.subprocess, "run", lambda cmd, **kw: bounced.append(cmd[3]) or None)

    bridge._restart_watchers()

    assert bounced == ["4242"]


def test_restart_watchers_skips_dead_wrapper(monkeypatch, tmp_path: Path) -> None:
    _seed_watcher_pidfiles(monkeypatch, tmp_path, {"review": 4242})

    def _dead(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(bridge.os, "kill", _dead)
    calls: list[list[str]] = []
    monkeypatch.setattr(bridge.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or None)

    bridge._restart_watchers()

    assert calls == []


def test_self_update_first_run_records_baseline_without_pull(monkeypatch, tmp_path: Path) -> None:
    sha_file = tmp_path / "last_sha"
    monkeypatch.setattr(bridge, "_LAST_SHA_FILE", sha_file)
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)

        class R:
            stdout = "abc123\n"

        return R()

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    assert bridge.self_update() == 0
    assert sha_file.read_text(encoding="utf-8") == "abc123"
    assert ["git", "pull", "--ff-only"] not in calls


def test_self_update_pulls_and_bounces_on_new_sha(monkeypatch, tmp_path: Path) -> None:
    sha_file = tmp_path / "last_sha"
    sha_file.write_text("old000", encoding="utf-8")
    monkeypatch.setattr(bridge, "_LAST_SHA_FILE", sha_file)
    _seed_watcher_pidfiles(monkeypatch, tmp_path, {})
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)

        class R:
            stdout = "new111\n"

        return R()

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    assert bridge.self_update() == 0
    assert ["git", "pull", "--ff-only"] in calls
    assert sha_file.read_text(encoding="utf-8") == "new111"


def test_self_update_fetches_before_comparing(monkeypatch, tmp_path: Path) -> None:
    """Deploy-gap regression: without a fetch, the local origin/main ref never
    moves and reviewer-merged fixes stay invisible until something else pulls."""
    sha_file = tmp_path / "last_sha"
    sha_file.write_text("old000", encoding="utf-8")
    monkeypatch.setattr(bridge, "_LAST_SHA_FILE", sha_file)
    _seed_watcher_pidfiles(monkeypatch, tmp_path, {})
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)

        class R:
            stdout = "new111\n"

        return R()

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    assert bridge.self_update() == 0
    fetch_idx = calls.index(["git", "fetch", "origin", "main", "--quiet"])
    revparse_idx = calls.index(["git", "rev-parse", "origin/main"])
    assert fetch_idx < revparse_idx


# ── config wiring ─────────────────────────────────────────────────────────────


def test_workers_yaml_pseudo_operator_section_is_valid() -> None:
    # The engine's schema is fail-closed (extra='forbid'); this pins that OC's
    # committed config actually parses — a typo'd key must fail HERE, not at
    # the next loop launch.
    from context_lifecycle.pseudo_operator import load_pseudo_operator_config

    cfg = load_pseudo_operator_config(bridge.REPO_ROOT / ".console" / "workers.yaml")
    assert cfg.loop_name == "oc"
    assert cfg.max_iterations > 0 and cfg.max_consecutive_failures > 0
    assert cfg.delay.kind == "schedule_state"
    assert cfg.delay.state_delays["HEALTHY"] == 3600
    assert cfg.hooks.pre_iteration and cfg.hooks.seed_cooldowns and cfg.hooks.on_cooldown
    assert cfg.hooks.session_end
    assert cfg.hooks.budget_guard
    assert cfg.prompt_path.exists()


# ── session-end ───────────────────────────────────────────────────────────────


class _FakeProc:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _fake_run_factory(monkeypatch, responses: dict[tuple[str, ...], _FakeProc]):
    """subprocess.run stub keyed on a command-prefix tuple; records all calls."""
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        for prefix, proc in responses.items():
            if tuple(cmd[: len(prefix)]) == prefix:
                return proc
        return _FakeProc()

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    return calls


def test_session_end_noop_on_main(monkeypatch) -> None:
    calls = _fake_run_factory(monkeypatch, {("git", "symbolic-ref"): _FakeProc(stdout="main\n")})
    assert bridge.session_end('{"iteration": 3}') == 0
    assert all(c[:2] != ["git", "checkout"] for c in calls)


def test_session_end_dirty_tree_untouched(monkeypatch) -> None:
    calls = _fake_run_factory(
        monkeypatch,
        {
            ("git", "symbolic-ref"): _FakeProc(stdout="oc-watchdog/x\n"),
            ("git", "status"): _FakeProc(stdout=" M src/foo.py\n"),
        },
    )
    assert bridge.session_end("{}") == 0
    assert all(c[:2] != ["git", "checkout"] for c in calls)


def test_session_end_returns_clean_checkout_and_opens_pr(monkeypatch) -> None:
    calls = _fake_run_factory(
        monkeypatch,
        {
            ("git", "symbolic-ref"): _FakeProc(stdout="oc-watchdog/x\n"),
            ("git", "status"): _FakeProc(stdout=""),
            ("git", "checkout"): _FakeProc(),
            ("git", "rev-parse", "--verify"): _FakeProc(returncode=0),
            ("git", "rev-list"): _FakeProc(stdout="1\n"),
            ("git", "log"): _FakeProc(stdout="fix: something\n"),
            ("gh", "pr", "list"): _FakeProc(stdout="[]"),
            ("gh", "pr", "create"): _FakeProc(stdout="https://x/pull/9"),
        },
    )
    assert bridge.session_end('{"iteration": 1}') == 0
    assert ["git", "checkout", "main"] in calls
    create = [c for c in calls if c[:3] == ["gh", "pr", "create"]]
    assert create and "fix: something" in create[0]


def test_session_end_unpushed_branch_no_pr(monkeypatch) -> None:
    calls = _fake_run_factory(
        monkeypatch,
        {
            ("git", "symbolic-ref"): _FakeProc(stdout="oc-watchdog/x\n"),
            ("git", "status"): _FakeProc(stdout=""),
            ("git", "checkout"): _FakeProc(),
            ("git", "rev-parse", "--verify"): _FakeProc(returncode=1),
        },
    )
    assert bridge.session_end("{}") == 0
    assert ["git", "checkout", "main"] in calls
    assert all(c[0] != "gh" for c in calls)


def test_session_end_existing_pr_not_duplicated(monkeypatch) -> None:
    calls = _fake_run_factory(
        monkeypatch,
        {
            ("git", "symbolic-ref"): _FakeProc(stdout="oc-watchdog/x\n"),
            ("git", "status"): _FakeProc(stdout=""),
            ("git", "checkout"): _FakeProc(),
            ("git", "rev-parse", "--verify"): _FakeProc(returncode=0),
            ("git", "rev-list"): _FakeProc(stdout="2\n"),
            ("gh", "pr", "list"): _FakeProc(stdout='[{"number": 448}]'),
        },
    )
    assert bridge.session_end("{}") == 0
    assert all(c[:3] != ["gh", "pr", "create"] for c in calls)


# ── budget-guard ──────────────────────────────────────────────────────────────


def test_budget_guard_ok_prints_null_cooldowns(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))  # no transcripts → no usage
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    assert bridge.budget_guard() == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"claude": None, "opus": None}


def test_budget_guard_exhausted_emits_reset_and_store_cooldown(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    from datetime import timezone as _tz

    projects = tmp_path / "projects" / "p1"
    projects.mkdir(parents=True)
    now = datetime.now(_tz.utc)
    projects.joinpath("s.jsonl").write_text(
        json.dumps(
            {
                "timestamp": now.isoformat(),
                "message": {"model": "claude-sonnet-5", "usage": {"output_tokens": 10_000}},
            }
        )
        + "\n"
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("OC_CLAUDE_BUDGET_CAP_WEIGHTED", "1000")
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    assert bridge.budget_guard() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["claude"] is not None and out["claude"] == out["opus"]
    until = UsageStore().worker_backend_cooldown_until("claude_code", now=now)
    assert until is not None and until > now


def test_main_dispatches_budget_guard_subcommand(monkeypatch, tmp_path: Path, capsys) -> None:
    # Guards audit F1: workers.yaml wires `... budget-guard`, so main() MUST route
    # it. A missing case would exit 2 and be swallowed as a silent no-op.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))  # no transcripts → no usage
    monkeypatch.setenv("OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setattr(bridge.sys, "argv", ["loop_bridge", "budget-guard"])
    assert bridge.main() == 0
    assert json.loads(capsys.readouterr().out) == {"claude": None, "opus": None}


def test_main_unknown_subcommand_exits_nonzero(monkeypatch, capsys) -> None:
    monkeypatch.setattr(bridge.sys, "argv", ["loop_bridge", "budget-gaurd"])  # typo
    assert bridge.main() == 2


def test_budget_guard_estimation_failure_is_loud_not_fatal(monkeypatch, capsys) -> None:
    # Audit pattern P-I: an estimator bug must surface a no-cooldown result and
    # exit 0 (degrade-never-halt), not raise out of the hook.
    def _boom(*_a, **_k):
        raise RuntimeError("transcript scan blew up")

    monkeypatch.setattr(
        "operations_center.execution.usage_budget.budget_status", _boom
    )
    assert bridge.budget_guard() == 0
    assert json.loads(capsys.readouterr().out) == {"claude": None, "opus": None}
