#!/usr/bin/env python3
"""OC platform watchdog loop controller.

Replaces /loop + ScheduleWakeup. Spawns a fresh bounded claude -p session
for each watchdog cycle. Context never accumulates; each session reconstructs
from .context/checkpoints/. The session writes .context/loop_schedule.json
at STEP 10 (instead of calling ScheduleWakeup) to communicate the adaptive delay.

Usage:
  python tools/loop/controller.py          # start (foreground; nohup & for overnight)
  python tools/loop/controller.py --stop   # write stop flag; current session finishes
  python tools/loop/controller.py --status # show lock state
"""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path("/home/dev/Documents/GitHub/OperationsCenter")
LOCK_PATH = REPO_ROOT / "logs/local/loop_controller.lock"
STOP_FLAG = REPO_ROOT / "logs/local/loop_stop.flag"
SCHEDULE_FILE = REPO_ROOT / ".context/loop_schedule.json"
LOG_FILE = REPO_ROOT / "logs/local/loop_controller.log"

# Fallback delays (seconds) when session doesn't write loop_schedule.json.
# Maps health state name → delay; also used as documentation of the full table.
STATE_DELAYS: dict[str, int] = {
    "CRITICAL": 180,
    "DEGRADED": 300,
    "STALLED": 600,
    "ACTIVE": 900,
    "PARKED_OPERATOR_BLOCKED": 1800,
    "HEALTHY": 3600,
}
DEFAULT_DELAY = 600  # conservative fallback (STALLED equivalent)

SESSION_PROMPT = (
    "You are running one iteration of the OC platform watchdog cycle, "
    "managed by an external controller. "
    "Full instructions are in docs/operator/watchdog_loop.md. "
    "Read .console/.context first. "
    "Do NOT call ScheduleWakeup — the controller manages timing. "
    "At STEP 10, assess platform health state, choose the appropriate delay "
    "from the cadence table, then write .context/loop_schedule.json with: "
    '{"delay_s": <int>, "state": "<STATE>", "reason": "<driving signal>"} '
    "and exit cleanly."
)

_stop = False


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(msg: str) -> None:
    line = f"[{_ts()}] {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def handle_signal(signum, frame) -> None:
    global _stop
    _stop = True
    STOP_FLAG.touch()
    _log(f"Signal {signum} received — stop flag set; waiting for current session to finish.")


def write_lock() -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = {
        "pid": os.getpid(),
        "started": _ts(),
        "hostname": socket.gethostname(),
        "purpose": "oc_watchdog_loop_controller",
        "repo_root": str(REPO_ROOT),
    }
    LOCK_PATH.write_text(json.dumps(lock, indent=2))


def check_and_acquire_lock() -> bool:
    """Return True if we acquired the lock (no live owner)."""
    if LOCK_PATH.exists():
        try:
            d = json.loads(LOCK_PATH.read_text())
            pid = d.get("pid")
            if pid:
                try:
                    os.kill(pid, 0)
                    _log(f"Lock held by live pid={pid} (started {d.get('started')}) — aborting.")
                    return False
                except ProcessLookupError:
                    _log(f"Stale lock (pid={pid} dead) — reclaiming.")
        except (json.JSONDecodeError, KeyError):
            _log("Malformed lock file — reclaiming.")
    write_lock()
    return True


def release_lock() -> None:
    LOCK_PATH.unlink(missing_ok=True)


def get_delay() -> int:
    """Read delay from schedule file written by the session at STEP 10."""
    try:
        if SCHEDULE_FILE.exists():
            s = json.loads(SCHEDULE_FILE.read_text())
            delay = s.get("delay_s")
            state = s.get("state", "?")
            reason = s.get("reason", "")
            if isinstance(delay, int) and delay > 0:
                _log(f"Schedule: state={state}, delay={delay}s — {reason}")
                return delay
    except Exception as e:
        _log(f"Failed to read schedule file: {e}")

    _log(f"No valid schedule file — using default {DEFAULT_DELAY}s (STALLED)")
    return DEFAULT_DELAY


def run_session() -> int:
    """Spawn one bounded claude -p session. Returns exit code."""
    cmd = ["claude", "-p", SESSION_PROMPT, "--output-format", "text"]
    _log(f"Spawning session: {' '.join(cmd[:2])} ...")
    # Source env before spawning so the session inherits OC credentials
    env = os.environ.copy()
    env_file = REPO_ROOT / ".env.operations-center.local"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env.setdefault(k.strip(), v.strip())
    proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env)
    return proc.returncode


def interruptible_sleep(seconds: int) -> None:
    """Sleep in small increments so SIGTERM is handled promptly."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not _stop and not STOP_FLAG.exists():
        time.sleep(min(5.0, deadline - time.monotonic()))


def cmd_status() -> None:
    if not LOCK_PATH.exists():
        print("No lock file — controller is not running.")
        return
    try:
        d = json.loads(LOCK_PATH.read_text())
        pid = d.get("pid")
        try:
            os.kill(pid, 0)
            print(f"ACTIVE: pid={pid}, started={d.get('started')}, host={d.get('hostname')}")
        except ProcessLookupError:
            print(f"STALE: pid={pid} is dead (lock from {d.get('started')})")
    except Exception as e:
        print(f"ERROR reading lock: {e}")
    if SCHEDULE_FILE.exists():
        try:
            s = json.loads(SCHEDULE_FILE.read_text())
            print(f"Last schedule: state={s.get('state')}, delay={s.get('delay_s')}s — {s.get('reason')}")
        except Exception:
            pass


def cmd_stop() -> None:
    STOP_FLAG.touch()
    print(f"Stop flag written to {STOP_FLAG}. Current session will finish, then controller exits.")


def main() -> None:
    parser = argparse.ArgumentParser(description="OC watchdog loop controller")
    parser.add_argument("--stop", action="store_true", help="Signal the controller to stop after the current session")
    parser.add_argument("--status", action="store_true", help="Show controller lock state and last schedule")
    args = parser.parse_args()

    if args.status:
        cmd_status()
        return

    if args.stop:
        cmd_stop()
        return

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    if not check_and_acquire_lock():
        sys.exit(1)

    STOP_FLAG.unlink(missing_ok=True)
    _log(f"OC watchdog loop controller started. pid={os.getpid()}")
    _log(f"Stop with: python tools/loop/controller.py --stop")
    _log(f"Status:    python tools/loop/controller.py --status")
    _log(f"Log:       {LOG_FILE}")

    iteration = 0
    try:
        while not _stop and not STOP_FLAG.exists():
            iteration += 1
            _log(f"--- Iteration {iteration} ---")

            # Clear stale schedule from previous session before spawning
            SCHEDULE_FILE.unlink(missing_ok=True)

            rc = run_session()
            _log(f"Session exited rc={rc}")

            if _stop or STOP_FLAG.exists():
                break

            delay = get_delay()
            _log(f"Sleeping {delay}s ...")
            interruptible_sleep(delay)
    finally:
        release_lock()
        _log(f"OC watchdog loop controller stopped after {iteration} iteration(s).")
        print(f"\nStopped. Log: {LOG_FILE}")


if __name__ == "__main__":
    main()
