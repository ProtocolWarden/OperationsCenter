#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""OC platform watchdog loop controller.

Replaces /loop + ScheduleWakeup. Spawns a fresh bounded agent session for each
watchdog cycle. Claude is the primary backend; both Claude and Codex backends
feed backend-specific cooldown windows when they hit usage limits, and the
controller switches to the other backend whenever possible. Context never
accumulates; each session reconstructs from the anchor manifest's
`.context/sessions/<sid>/checkpoints/`. The session writes
`tools/loop/loop_schedule.json` at STEP 10 (instead of calling ScheduleWakeup)
to communicate the adaptive delay; this is controller runtime state,
collocated with the controller script.

Usage:
  python tools/loop/controller.py          # start (foreground; nohup & for overnight)
  python tools/loop/controller.py --stop   # write stop flag; current session finishes
  python tools/loop/controller.py --status # show lock state
"""

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

REPO_ROOT = Path("/home/dev/Documents/GitHub/OperationsCenter")
LOCK_PATH = REPO_ROOT / "logs/local/loop_controller.lock"
STATE_PATH = REPO_ROOT / "logs/local/loop_controller_state.json"
WATCHDOG_LOOP_LOCK = REPO_ROOT / "logs/local/watchdog_loop.lock"
STOP_FLAG = REPO_ROOT / "logs/local/loop_stop.flag"
SCHEDULE_FILE = REPO_ROOT / "tools/loop/loop_schedule.json"
LOG_FILE = REPO_ROOT / "logs/local/loop_controller.log"
SESSION_LOG_DIR = REPO_ROOT / "logs/local/sessions"
SESSION_PROMPT_FILE = REPO_ROOT / "tools/loop/oc_session_prompt.txt"
CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_EFFORT = "medium"
CODEX_MODEL = "gpt-5.4"
CODEX_EFFORT = "medium"

# Hard ceiling on a single session. A normal cycle runs 10-20 min.
# If the session hasn't exited after this many seconds it is hung
# (e.g. self-referential pgrep loop) and must be killed.
SESSION_TIMEOUT_SECONDS = 45 * 60  # 45 minutes

# Fallback delays (seconds) when session doesn't write loop_schedule.json.
STATE_DELAYS: dict[str, int] = {
    "CRITICAL": 180,
    "DEGRADED": 300,
    "STALLED": 600,
    "ACTIVE": 900,
    "PARKED_OPERATOR_BLOCKED": 1800,
    "HEALTHY": 3600,
}
DEFAULT_DELAY = 600  # conservative fallback (STALLED equivalent)


def load_session_prompt() -> str:
    try:
        return SESSION_PROMPT_FILE.read_text()
    except OSError as e:
        raise SystemExit(f"Cannot read session prompt file {SESSION_PROMPT_FILE}: {e}")


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


def _fallback_command_candidates(command: str) -> list[Path]:
    home = Path.home()
    candidates: list[Path] = []
    if command == "claude":
        candidates.extend(
            [
                home / ".local" / "bin" / "claude",
                home / "bin" / "claude",
            ]
        )
    elif command == "codex":
        candidates.extend(
            [
                home / ".local" / "bin" / "codex",
                home / "bin" / "codex",
            ]
        )
        candidates.extend(sorted((home / ".nvm" / "versions" / "node").glob("*/bin/codex")))
    return candidates


def _resolve_command(command: str) -> str | None:
    resolved = shutil.which(command)
    if resolved is not None:
        return resolved
    for candidate in _fallback_command_candidates(command):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _command_available(command: str) -> bool:
    return _resolve_command(command) is not None


def _anchor_via_cl(env: dict[str, str]) -> None:
    """Anchor the loop's session at this repo's owning manifest via CL.

    Runs `cl session start` (no arg → RepoGraph resolves cwd→manifest) and merges
    its exported CL_ANCHOR/CL_SESSION_ID into ``env``. No-op if the repo isn't
    hooked to a manifest or `cl` is unavailable — the loop then runs unanchored,
    exactly as before, so cl_wrap stays a no-op.
    """
    try:
        out = subprocess.run(
            ["cl", "session", "start"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return
    if out.returncode != 0:
        return
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.startswith("export ") and "=" in line:
            key, _, val = line[len("export "):].partition("=")
            env[key.strip()] = val.strip().strip("'\"")


# Stable lineage for this loop's session-boundary cognition (resumes across runs
# within an anchored session).
_CL_LINEAGE = "oc-loop"


def _cl_session_boundary(backend: str, env: dict[str, str]) -> bool:
    """True for backends that need session-boundary CL (no per-tool hooks).

    claude runs the ContextGuard hooks per tool call, so it does NOT use the
    boundary hydrate/capture path. codex (PostToolUse-only, no live PreToolUse)
    and aider (no hooks) DO. Gated on an active anchor.
    """
    return backend != "claude" and bool(env.get("CL_ANCHOR"))


def _cl_hydrate(backend: str, env: dict[str, str], iteration: int, prompt: str) -> str:
    """Prepend prior ContextLifecycle context to the prompt for non-hook backends.

    No-op for claude, when unanchored, or if `cl` is unavailable.
    """
    if not _cl_session_boundary(backend, env):
        return prompt
    work_item = json.dumps({"loop": "oc", "iteration": iteration, "backend": backend})
    try:
        out = subprocess.run(
            ["cl", "context", "hydrate", "--lineage", _CL_LINEAGE, "--work-item", work_item],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return prompt
    if out.returncode != 0 or not out.stdout.strip():
        return prompt
    return (
        "# Prior session context (ContextLifecycle hydrate)\n```json\n"
        + out.stdout.strip()
        + "\n```\n\n"
        + prompt
    )


def _cl_capture(backend: str, env: dict[str, str], iteration: int, exit_code: int, log_path: Path) -> None:
    """Capture the session result into ContextLifecycle for non-hook backends. No-op otherwise."""
    if not _cl_session_boundary(backend, env):
        return
    result = json.dumps(
        {"loop": "oc", "iteration": iteration, "backend": backend,
         "exit_code": exit_code, "log": str(log_path)}
    )
    try:
        subprocess.run(
            ["cl", "context", "capture", "--lineage", _CL_LINEAGE, "--result", result],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _session_env(backend: str) -> dict[str, str]:
    env = os.environ.copy()
    _anchor_via_cl(env)
    executable = _resolve_command(backend)
    if executable is None:
        return env
    executable_path = Path(executable)
    path_candidates = [str(executable_path.parent)]
    resolved_parent = str(executable_path.resolve().parent)
    if resolved_parent not in path_candidates:
        path_candidates.append(resolved_parent)
    current_path = env.get("PATH", "")
    path_parts = [part for part in current_path.split(os.pathsep) if part]
    prepend = [part for part in path_candidates if part not in path_parts]
    if prepend:
        env["PATH"] = (
            os.pathsep.join([*prepend, *path_parts])
            if path_parts
            else os.pathsep.join(prepend)
        )
    return env


def _session_command(backend: str, prompt: str) -> list[str]:
    if backend == "claude":
        executable = _resolve_command("claude") or "claude"
        return [
            executable,
            "-p",
            prompt,
            "--model",
            CLAUDE_MODEL,
            "--effort",
            CLAUDE_EFFORT,
            "--dangerously-skip-permissions",
            "--output-format",
            "text",
        ]
    if backend == "codex":
        executable = _resolve_command("codex") or "codex"
        return [
            executable,
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--cd",
            str(REPO_ROOT),
            "--model",
            CODEX_MODEL,
            "-c",
            f'model_reasoning_effort="{CODEX_EFFORT}"',
            prompt,
        ]
    raise ValueError(f"Unknown backend '{backend}'")


def _session_log_path(iteration: int, backend: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return SESSION_LOG_DIR / f"iter_{iteration:04d}_{ts}_{backend}.log"


def _backend_available(backend: str, cooldowns: dict[str, datetime | None]) -> bool:
    until = cooldowns.get(backend)
    return _command_available(backend) and (
        until is None or datetime.now(timezone.utc) >= until
    )


def _clear_expired_cooldowns(cooldowns: dict[str, datetime | None]) -> None:
    now = datetime.now(timezone.utc)
    for backend, until in list(cooldowns.items()):
        if until is not None and now >= until:
            cooldowns[backend] = None
            _log(f"{backend.capitalize()} cooldown expired — backend runnable again.")


def _select_backend(cooldowns: dict[str, datetime | None]) -> str | None:
    if _backend_available("claude", cooldowns):
        return "claude"
    if _backend_available("codex", cooldowns):
        return "codex"
    if not _command_available("claude") and not _command_available("codex"):
        raise SystemExit("Neither claude nor codex CLI is available on PATH.")
    return None


def _next_backend_reset(cooldowns: dict[str, datetime | None]) -> datetime | None:
    now = datetime.now(timezone.utc)
    future_resets = [dt for dt in cooldowns.values() if dt is not None and dt > now]
    if not future_resets:
        return None
    return min(future_resets)


def _sleep_until_backend_reset(cooldowns: dict[str, datetime | None]) -> bool:
    reset_dt = _next_backend_reset(cooldowns)
    if reset_dt is None:
        return False
    delay = max(
        60,
        int((reset_dt - datetime.now(timezone.utc)).total_seconds())
        + _RATE_LIMIT_BUFFER,
    )
    _log(
        f"All available backends are cooling down until "
        f"{reset_dt.strftime('%Y-%m-%dT%H:%M:%SZ')} UTC; sleeping {delay}s ({delay // 60}m)"
    )
    interruptible_sleep(delay)
    return True


def _alternate_backend(backend: str) -> str:
    return "codex" if backend == "claude" else "claude"


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


def write_runtime_state(
    cooldowns: dict[str, datetime | None],
    runnable_backend: str | None,
    *,
    preferred_backend: str = "claude",
) -> None:
    state = {
        "updated": _ts(),
        "preferred_backend": preferred_backend,
        "runnable_backend": runnable_backend,
        "backend_cooldowns": {
            backend: dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt is not None else None
            for backend, dt in cooldowns.items()
        },
    }
    STATE_PATH.write_text(json.dumps(state, indent=2))


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
    STATE_PATH.unlink(missing_ok=True)
    WATCHDOG_LOOP_LOCK.unlink(missing_ok=True)


def write_watchdog_loop_lock() -> None:
    """Write watchdog_loop.lock owned by this controller process.

    Sessions previously acquired this lock themselves via watchdog-loop-acquire,
    but stored PPID (the spawning claude process) rather than a stable pid —
    causing stale-lock failures after session exit. The controller is the correct
    owner: it is live for the full loop lifetime and has correct reclaim logic.
    """
    lock = {
        "pid": os.getpid(),
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hostname": socket.gethostname(),
        "repo_root": str(REPO_ROOT),
        "purpose": "OC Platform Watchdog Loop",
    }
    WATCHDOG_LOOP_LOCK.parent.mkdir(parents=True, exist_ok=True)
    WATCHDOG_LOOP_LOCK.write_text(json.dumps(lock, indent=2))


_TIMEZONE_RESET_RE = re.compile(
    r"resets\s+(\d{1,2}(?::\d{2})?(?:am|pm))\s+\(([^)]+)\)", re.IGNORECASE
)
_ISO_RESET_RE = re.compile(
    r"resets?(?:\s+at)?\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z)",
    re.IGNORECASE,
)
_RELATIVE_RESET_RE = re.compile(
    r"(?:try again|retry|resets?|reset|available again)[^\n]{0,80}?\bin\s+"
    r"(?:(?P<hours>\d+)\s*h(?:ours?)?)?\s*"
    r"(?:(?P<minutes>\d+)\s*m(?:in(?:ute)?s?)?)?\s*"
    r"(?:(?P<seconds>\d+)\s*s(?:ec(?:ond)?s?)?)?",
    re.IGNORECASE,
)
_LIMIT_SIGNAL_RE = re.compile(
    r"rate limit|usage limit|weekly limit|quota|too many requests|429",
    re.IGNORECASE,
)
_RATE_LIMIT_BUFFER = 120  # seconds to wait after the stated reset time


def parse_rate_limit_reset(session_log: Path, backend: str = "claude") -> datetime | None:
    """Return UTC datetime when a backend becomes runnable again after a limit hit."""
    try:
        text = session_log.read_text(errors="replace")
        m = _TIMEZONE_RESET_RE.search(text)
        if m:
            time_str, tz_name = m.group(1).lower(), m.group(2)
            try:
                tz = ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                _log(
                    f"Unknown timezone '{tz_name}' in {backend} limit message — cannot parse reset time."
                )
                return None
            now_local = datetime.now(tz)
            time_format = "%I:%M%p" if ":" in time_str else "%I%p"
            parsed = datetime.strptime(time_str, time_format)  # noqa: DTZ007 — naive; immediately reused for h/m only
            reset_local = now_local.replace(
                hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0
            )
            if reset_local <= now_local:
                reset_local += timedelta(days=1)
            return reset_local.astimezone(timezone.utc)

        m = _ISO_RESET_RE.search(text)
        if m:
            iso_text = m.group(1)
            return datetime.fromisoformat(iso_text.replace("Z", "+00:00")).astimezone(
                timezone.utc
            )

        m = _RELATIVE_RESET_RE.search(text)
        if m:
            hours = int(m.group("hours") or 0)
            minutes = int(m.group("minutes") or 0)
            seconds = int(m.group("seconds") or 0)
            delta = timedelta(hours=hours, minutes=minutes, seconds=seconds)
            if delta.total_seconds() > 0:
                return datetime.now(timezone.utc) + delta

        if _LIMIT_SIGNAL_RE.search(text):
            _log(
                f"{backend.capitalize()} limit detected but no reset time was parseable from {session_log.name}."
            )
        return None
    except Exception as e:
        _log(f"Failed to parse {backend} rate-limit reset time: {e}")
        return None


def _handle_backend_limit(
    backend: str, session_log: Path, cooldowns: dict[str, datetime | None]
) -> bool:
    reset_dt = parse_rate_limit_reset(session_log, backend)
    if reset_dt is None:
        return False
    cooldowns[backend] = reset_dt
    _log(
        f"{backend.capitalize()} limit detected — backend cooling down until "
        f"{reset_dt.strftime('%Y-%m-%dT%H:%M:%SZ')} UTC."
    )
    return True


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


def run_session(iteration: int, backend: str = "claude") -> tuple[int, Path]:
    """Spawn one bounded agent session. Returns (exit_code, session_log_path)."""
    prompt = load_session_prompt()
    env = _session_env(backend)
    prompt = _cl_hydrate(backend, env, iteration, prompt)
    cmd = _session_command(backend, prompt)

    SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    session_log = _session_log_path(iteration, backend)

    _log(
        f"Spawning {backend} session "
        f"(prompt: {SESSION_PROMPT_FILE.name}, {len(prompt)} chars) → {session_log.name}"
    )
    env_file = REPO_ROOT / ".env.operations-center.local"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env.setdefault(k.strip(), v.strip())

    with session_log.open("w") as log_fh:
        proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            env=env,
            stdout=log_fh,
            stderr=log_fh,
        )
        try:
            proc.wait(timeout=SESSION_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            _log(
                f"[WARN] Session exceeded {SESSION_TIMEOUT_SECONDS}s timeout — "
                f"killed (likely hung subprocess, e.g. self-referential pgrep loop). "
                f"Log: {session_log.name}"
            )
            _cl_capture(backend, env, iteration, -1, session_log)
            return -1, session_log

    _cl_capture(backend, env, iteration, proc.returncode, session_log)
    return proc.returncode, session_log


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
    if STATE_PATH.exists():
        try:
            s = json.loads(STATE_PATH.read_text())
            print(f"Preferred backend: {s.get('preferred_backend')}")
            print(f"Current runnable backend: {s.get('runnable_backend')}")
            cooldowns = s.get("backend_cooldowns", {})
            for backend in ("claude", "codex"):
                print(f"{backend} cooldown until: {cooldowns.get(backend)}")
        except Exception as e:
            print(f"ERROR reading runtime state: {e}")
    if SCHEDULE_FILE.exists():
        try:
            s = json.loads(SCHEDULE_FILE.read_text())
            print(
                f"Last schedule: state={s.get('state')}, delay={s.get('delay_s')}s — {s.get('reason')}"
            )
        except Exception:
            pass


def cmd_stop() -> None:
    STOP_FLAG.touch()
    print(f"Stop flag written to {STOP_FLAG}. Current session will finish, then controller exits.")


def main() -> None:
    parser = argparse.ArgumentParser(description="OC watchdog loop controller")
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Signal the controller to stop after the current session",
    )
    parser.add_argument(
        "--status", action="store_true", help="Show controller lock state and last schedule"
    )
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

    write_watchdog_loop_lock()
    STOP_FLAG.unlink(missing_ok=True)
    STATE_PATH.unlink(missing_ok=True)
    _log(f"OC watchdog loop controller started. pid={os.getpid()}")
    _log("Stop with: python tools/loop/controller.py --stop")
    _log("Status:    python tools/loop/controller.py --status")
    _log(f"Log:       {LOG_FILE}")

    iteration = 0
    cooldowns: dict[str, datetime | None] = {"claude": None, "codex": None}
    try:
        while not _stop and not STOP_FLAG.exists():
            iteration += 1
            _log(f"--- Iteration {iteration} ---")

            # Clear stale schedule from previous session before spawning
            SCHEDULE_FILE.unlink(missing_ok=True)

            _clear_expired_cooldowns(cooldowns)
            backend = _select_backend(cooldowns)
            write_runtime_state(cooldowns, backend)
            if backend is None:
                if _sleep_until_backend_reset(cooldowns):
                    write_runtime_state(cooldowns, None)
                    continue
                _log("No runnable backend is currently available.")
                write_runtime_state(cooldowns, None)
                break
            rc, session_log = run_session(iteration, backend)
            _log(f"{backend.capitalize()} session exited rc={rc}")

            if _stop or STOP_FLAG.exists():
                break

            if rc != 0:
                if _handle_backend_limit(backend, session_log, cooldowns):
                    write_runtime_state(cooldowns, None)
                    alternate = _alternate_backend(backend)
                    if _backend_available(alternate, cooldowns):
                        _log(
                            f"{backend.capitalize()} is cooling down; "
                            f"running immediate {alternate.capitalize()} fallback."
                        )
                        write_runtime_state(cooldowns, alternate)
                        rc, session_log = run_session(iteration, alternate)
                        backend = alternate
                        _log(f"{alternate.capitalize()} fallback session exited rc={rc}")
                        if _stop or STOP_FLAG.exists():
                            break
                        if rc != 0 and _handle_backend_limit(backend, session_log, cooldowns):
                            write_runtime_state(cooldowns, None)
                            if _sleep_until_backend_reset(cooldowns):
                                continue
                    else:
                        if _sleep_until_backend_reset(cooldowns):
                            continue

            delay = get_delay()
            _log(f"Sleeping {delay}s ...")
            interruptible_sleep(delay)
    finally:
        release_lock()
        _log(f"OC watchdog loop controller stopped after {iteration} iteration(s).")
        print(f"\nStopped. Log: {LOG_FILE}")


if __name__ == "__main__":
    main()
