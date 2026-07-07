# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""loop_bridge — OC-specific hooks for the PseudoOperator loop engine.

The loop engine (ContextLifecycle, ``cl loop``) is repo-agnostic; everything
OC-specific from the old ``tools/loop/controller.py`` lives here and is
invoked by the engine as hook commands configured in ``.console/workers.yaml``:

  seed-cooldowns   — print JSON ``{backend: iso8601|null}`` from the executor
                     usage store, so a restarted loop remembers limits found
                     by previous runs (hook: ``seed_cooldowns``).
  on-cooldown JSON — record a controller-detected cooldown into the usage
                     store, so the OC CLI and OperatorConsole pane show
                     per-model limit state (hook: ``on_cooldown``).
  self-update      — detect code merged since the last check, ``git pull``
                     and bounce the watcher children so fixes take effect
                     without manual intervention (hook: ``pre_iteration``).

All commands are best-effort by contract (the engine logs and continues on
hook failure) but exit non-zero on genuine errors so the failure is visible.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[4]
WATCH_DIR = REPO_ROOT / "logs/local/watch-all"
_LAST_SHA_FILE = REPO_ROOT / "tools/loop/state/last_update_sha"

_WATCHER_ROLES = ["goal", "test", "improve", "propose", "review", "spec", "intake", "watchdog"]
# Substring that uniquely identifies a watcher's Python child process on the
# command line. Used to bounce the child without touching its supervisor
# wrapper or sibling heartbeat loops.
_WATCHER_CHILD_MATCH = "operations_center.entrypoints"

# Controller backend → (worker_backend, model) for the status surfaces.
_WORKER_BACKEND_FOR: dict[str, tuple[str, str]] = {
    "claude": ("claude_code", "sonnet"),
    "opus": ("claude_code", "opus"),
    "codex": ("codex_cli", "codex"),
}


# ── seed-cooldowns ────────────────────────────────────────────────────────────


def _cooldown_details(snapshot: dict, backend_key: str) -> list[dict]:
    raw = snapshot.get(backend_key)
    details = raw.get("cooldowns") if isinstance(raw, dict) else None
    if not isinstance(details, list):
        return []
    return [d for d in details if isinstance(d, dict)]


def seed_cooldowns() -> int:
    """Print ``{backend: iso8601|null}`` seeded from the executor usage store."""
    from operations_center.execution.usage_store import UsageStore

    now = datetime.now(timezone.utc)
    snapshot = UsageStore().current_worker_backend_cooldowns(now=now)
    out: dict[str, str | None] = {"claude": None, "opus": None, "codex": None}

    def apply(backend: str, reset_raw: object) -> None:
        if not isinstance(reset_raw, str):
            return
        try:
            reset_at = datetime.fromisoformat(reset_raw.replace("Z", "+00:00")).astimezone(
                timezone.utc
            )
        except ValueError:
            return
        if reset_at <= now:
            return
        current = out.get(backend)
        if current is None or reset_at.strftime("%Y-%m-%dT%H:%M:%SZ") > current:
            out[backend] = reset_at.strftime("%Y-%m-%dT%H:%M:%SZ")

    for detail in _cooldown_details(snapshot, "claude_code"):
        model = detail.get("model")
        if detail.get("limit_kind") == "model_weekly" and model == "sonnet":
            apply("claude", detail.get("reset_at"))
        elif detail.get("limit_kind") == "model_weekly" and model == "opus":
            apply("opus", detail.get("reset_at"))
        elif detail.get("limit_kind") != "model_weekly":
            apply("claude", detail.get("reset_at"))
            apply("opus", detail.get("reset_at"))
    for detail in _cooldown_details(snapshot, "codex_cli"):
        apply("codex", detail.get("reset_at"))

    print(json.dumps(out, ensure_ascii=False))
    return 0


# ── on-cooldown ───────────────────────────────────────────────────────────────


def on_cooldown(payload_json: str) -> int:
    """Record a controller-detected cooldown into the executor usage store.

    Payload (from the engine): ``{"backend", "reset_at", "limit_kind", "model"}``.
    The engine's ``model`` is the full model id; the usage store wants the
    short name from the backend mapping.
    """
    from operations_center.execution.usage_store import UsageStore

    payload = json.loads(payload_json)
    backend = payload.get("backend", "")
    worker_backend, short_model = _WORKER_BACKEND_FOR.get(backend, (None, None))
    if worker_backend is None:
        logger.warning("loop_bridge: unknown backend %r — cooldown not recorded", backend)
        return 0
    reset_at = datetime.fromisoformat(str(payload["reset_at"]).replace("Z", "+00:00")).astimezone(
        timezone.utc
    )
    limit_kind = str(payload.get("limit_kind") or "unknown")
    model = short_model if payload.get("model") else None
    UsageStore().record_worker_backend_cooldown(
        worker_backend=worker_backend,
        reset_at=reset_at,
        now=datetime.now(timezone.utc),
        limit_kind=limit_kind,
        model=model,
    )
    return 0


# ── self-update ───────────────────────────────────────────────────────────────


def _restart_watchers() -> None:
    """Bounce each watcher's Python child so its supervisor wrapper relaunches
    it against the freshly-pulled code.

    The pid file holds the *wrapper* pid (a ``setsid bash`` auto-restart loop
    that traps SIGTERM with ``exit 0``) — signalling it kills the supervisor
    and the watcher never comes back. Instead SIGTERM the wrapper's Python
    children (matched by command line so sibling heartbeat loops survive); the
    wrapper's ``wait`` returns and it relaunches against current source. The
    watchdog is never bounced: it is the backstop that revives a genuinely
    dead wrapper and must outlive every restart.
    """
    for role in _WATCHER_ROLES:
        if role == "watchdog":
            continue
        pid_file = WATCH_DIR / f"{role}.pid"
        if not pid_file.exists():
            continue
        try:
            wrapper_pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        try:
            os.kill(wrapper_pid, 0)
        except ProcessLookupError:
            # Dead wrapper: the watchdog revives it from the stale pid file.
            continue
        except PermissionError:
            pass
        try:
            subprocess.run(
                ["pkill", "-TERM", "-P", str(wrapper_pid), "-f", _WATCHER_CHILD_MATCH],
                capture_output=True,
                timeout=30,
            )
            logger.info(
                "loop_bridge: bounced watcher '%s' child (wrapper pid %s) — code updated",
                role,
                wrapper_pid,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort per role
            logger.warning("loop_bridge: failed to bounce watcher '%s': %s", role, exc)


def self_update() -> int:
    """git-pull + watcher restart when origin/main moved since the last check."""
    # rev-parse reads the LOCAL origin/main ref; without a fetch it only moves
    # when something else happens to fetch, so reviewer-merged fixes stayed
    # invisible for a full cycle (the iteration-3 deploy gap, 2026-07-07).
    # Fetch failure (offline) is tolerated — we proceed on the stale ref.
    subprocess.run(
        ["git", "fetch", "origin", "main", "--quiet"],
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=120,
        check=False,
    )
    current_sha = subprocess.run(
        ["git", "rev-parse", "origin/main"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.strip()
    if not current_sha:
        return 0
    last_sha = ""
    try:
        last_sha = _LAST_SHA_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if not last_sha:
        # First run: record the baseline without pulling.
        _LAST_SHA_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LAST_SHA_FILE.write_text(current_sha, encoding="utf-8")
        return 0
    if current_sha == last_sha:
        return 0
    logger.info(
        "loop_bridge: code updated %s→%s — pulling and restarting watchers",
        last_sha[:8],
        current_sha[:8],
    )
    subprocess.run(["git", "pull", "--ff-only"], cwd=REPO_ROOT, capture_output=True, timeout=300)
    _LAST_SHA_FILE.write_text(current_sha, encoding="utf-8")
    _restart_watchers()
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = sys.argv[1:]
    if not args:
        print("usage: loop_bridge {seed-cooldowns|on-cooldown JSON|self-update}", file=sys.stderr)
        return 2
    cmd = args[0]
    if cmd == "seed-cooldowns":
        return seed_cooldowns()
    if cmd == "on-cooldown":
        if len(args) < 2:
            print("on-cooldown requires a JSON payload argument", file=sys.stderr)
            return 2
        return on_cooldown(args[1])
    if cmd == "self-update":
        return self_update()
    print(f"unknown loop_bridge command {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
