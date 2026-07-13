# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Event-driven pipeline trigger — reruns autonomy-cycle when repo state changes.

Rather than relying entirely on scheduled cron or manual invocations, this daemon
watches for trigger events and fires the autonomy pipeline reactively:

Trigger sources:
  1. Git repo fetch — ``repos[].local_path/.git/FETCH_HEAD`` mtime advances
  2. Error ingest — ``state/error_ingest_dedup.json`` is updated
  3. CI failure sentinel — new failure files appear in ``tools/report/executor_plane/``

When any trigger fires, ``autonomy-cycle --config <config> --execute`` is run
(unless the last run was within ``min_interval_seconds`` to prevent thrash).

Usage::

    python -m operations_center.entrypoints.pipeline_trigger.main \\
        --config config/operations_center.local.yaml \\
        [--execute]               # pass --execute to enable real task creation
        [--min-interval 300]      # minimum seconds between triggered runs (default 300)
        [--poll-interval 30]      # how often to check for trigger files (default 30)

The trigger writes its state to ``state/pipeline_trigger_state.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from operations_center.entrypoints.heartbeat import touch_liveness, write_heartbeat

_TRIGGER_STATE_PATH = Path("state/pipeline_trigger_state.json")
_DEFAULT_MIN_INTERVAL = 300  # 5 minutes between triggered runs
_DEFAULT_POLL_INTERVAL = 30  # check every 30 seconds
_HEARTBEAT_INTERVAL_SECONDS = 30

logger = logging.getLogger(__name__)


def _load_state() -> dict:
    try:
        return json.loads(_TRIGGER_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    _TRIGGER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TRIGGER_STATE_PATH.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _get_trigger_sources(config_path: str) -> list[Path]:
    """Build list of files to watch for changes."""
    sources: list[Path] = []

    # Error ingest dedup state file
    sources.append(Path("state/error_ingest_dedup.json"))

    # CI failure artifacts directory (watch for new subdirs)
    sources.append(Path("tools/report/executor_plane"))

    # Git repo FETCH_HEAD files
    try:
        from operations_center.config import load_settings

        settings = load_settings(config_path)
        for repo_cfg in settings.repos.values():
            lp = getattr(repo_cfg, "local_path", None)
            if lp:
                fetch_head = Path(str(lp)) / ".git" / "FETCH_HEAD"
                sources.append(fetch_head)
    except Exception:
        pass

    return sources


def _snapshot_mtimes(sources: list[Path]) -> dict[str, float]:
    """Return {path: mtime} for all existing trigger files/dirs."""
    result: dict[str, float] = {}
    for p in sources:
        try:
            if p.exists():
                if p.is_dir():
                    # For directories: use the count of immediate children as the signal
                    result[str(p)] = float(sum(1 for _ in p.iterdir()))
                else:
                    result[str(p)] = p.stat().st_mtime
        except Exception:
            pass
    return result


def _has_changed(old: dict[str, float], new: dict[str, float]) -> list[str]:
    """Return list of paths that changed between snapshots."""
    changed: list[str] = []
    for path, new_val in new.items():
        old_val = old.get(path)
        if old_val is None or new_val != old_val:
            changed.append(path)
    return changed


def _write_heartbeat(
    status_dir: Path | None,
    *,
    success: bool,
    status: str,
    error: str | None = None,
) -> None:
    if status_dir is None:
        return
    write_heartbeat(status_dir, "propose", status=status, success=success, error=error)


def _heartbeat_loop(status_dir: Path | None, stop_event: threading.Event) -> None:
    if status_dir is None:
        return
    while not stop_event.wait(_HEARTBEAT_INTERVAL_SECONDS):
        touch_liveness(status_dir, "propose", status="executing")


def _run_pipeline(config_path: str, *, execute: bool, status_dir: Path | None = None) -> bool:
    """Run autonomy-cycle. Returns True on success."""
    cmd = [
        sys.executable,
        "-m",
        "operations_center.entrypoints.autonomy_cycle.main",
        "--config",
        config_path,
    ]
    if execute:
        cmd.append("--execute")

    logger.info(
        json.dumps(
            {
                "event": "pipeline_trigger_running",
                "command": " ".join(cmd),
                "triggered_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
        )
    )
    stop_event = threading.Event()
    heartbeat_thread: threading.Thread | None = None
    try:
        touch_liveness(status_dir, "propose", status="executing")
        if status_dir is not None:
            heartbeat_thread = threading.Thread(
                target=_heartbeat_loop, args=(status_dir, stop_event), daemon=True
            )
            heartbeat_thread.start()
        result = subprocess.run(cmd, timeout=600, capture_output=False)
        success = result.returncode == 0
        _write_heartbeat(
            status_dir,
            success=success,
            status="idle" if success else "error",
            error=None if success else f"autonomy_cycle_exit_{result.returncode}",
        )
        logger.info(
            json.dumps(
                {
                    "event": "pipeline_trigger_complete",
                    "returncode": result.returncode,
                    "success": success,
                },
                ensure_ascii=False,
            )
        )
        return success
    except subprocess.TimeoutExpired:
        _write_heartbeat(
            status_dir,
            success=False,
            status="error",
            error="autonomy_cycle_timeout",
        )
        logger.warning(json.dumps({"event": "pipeline_trigger_timeout"}, ensure_ascii=False))
        return False
    except Exception as exc:
        _write_heartbeat(status_dir, success=False, status="error", error=str(exc))
        logger.warning(
            json.dumps({"event": "pipeline_trigger_error", "error": str(exc)}, ensure_ascii=False)
        )
        return False
    finally:
        stop_event.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1)


def run_trigger_loop(
    config_path: str,
    *,
    execute: bool = False,
    min_interval_seconds: int = _DEFAULT_MIN_INTERVAL,
    poll_interval_seconds: int = _DEFAULT_POLL_INTERVAL,
    status_dir: Path | None = None,
) -> None:
    """Watch trigger sources and fire the pipeline on change.

    Runs until interrupted (KeyboardInterrupt or SIGTERM).
    """
    sources = _get_trigger_sources(config_path)
    state = _load_state()
    last_run_at: float = float(state.get("last_run_at", 0))
    snapshot = _snapshot_mtimes(sources)

    logger.info(
        json.dumps(
            {
                "event": "pipeline_trigger_started",
                "sources_count": len(sources),
                "min_interval_seconds": min_interval_seconds,
                "execute": execute,
            },
            ensure_ascii=False,
        )
    )
    _write_heartbeat(status_dir, success=True, status="idle")

    while True:
        time.sleep(poll_interval_seconds)

        new_snapshot = _snapshot_mtimes(sources)
        changed = _has_changed(snapshot, new_snapshot)

        if not changed:
            _write_heartbeat(status_dir, success=True, status="idle")
            continue

        now = time.time()
        elapsed_since_last = now - last_run_at

        if elapsed_since_last < min_interval_seconds:
            logger.info(
                json.dumps(
                    {
                        "event": "pipeline_trigger_debounced",
                        "changed": changed,
                        "next_run_in_seconds": round(min_interval_seconds - elapsed_since_last),
                    },
                    ensure_ascii=False,
                )
            )
            snapshot = new_snapshot
            _write_heartbeat(status_dir, success=True, status="idle")
            continue

        logger.info(
            json.dumps(
                {
                    "event": "pipeline_trigger_fired",
                    "changed": changed,
                    "elapsed_since_last_run": round(elapsed_since_last),
                },
                ensure_ascii=False,
            )
        )

        _run_pipeline(config_path, execute=execute, status_dir=status_dir)
        last_run_at = time.time()
        state["last_run_at"] = last_run_at
        state["last_triggered_by"] = changed
        state["last_triggered_at"] = datetime.now(UTC).isoformat()
        _save_state(state)
        snapshot = new_snapshot


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Event-driven pipeline trigger. "
            "Watches for repo changes and fires autonomy-cycle reactively."
        )
    )
    parser.add_argument("--config", required=True, help="Path to operations_center.local.yaml")
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Pass --execute to autonomy-cycle (enables real Plane task creation). Default: dry-run.",
    )
    parser.add_argument(
        "--min-interval",
        type=int,
        default=_DEFAULT_MIN_INTERVAL,
        dest="min_interval",
        help=f"Minimum seconds between triggered runs (default: {_DEFAULT_MIN_INTERVAL}).",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=_DEFAULT_POLL_INTERVAL,
        dest="poll_interval",
        help=f"How often to check for trigger file changes in seconds (default: {_DEFAULT_POLL_INTERVAL}).",
    )
    parser.add_argument(
        "--status-dir",
        type=Path,
        default=None,
        help="Directory for heartbeat_propose.json",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    os.environ.setdefault("OPERATIONS_CENTER_CONFIG", args.config)

    try:
        run_trigger_loop(
            args.config,
            execute=args.execute,
            min_interval_seconds=args.min_interval,
            poll_interval_seconds=args.poll_interval,
            status_dir=args.status_dir,
        )
    except KeyboardInterrupt:
        logger.info(json.dumps({"event": "pipeline_trigger_stopped"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
